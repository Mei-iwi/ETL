"""Bounded immutable query representations; no I/O, translation or ACL state."""
from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import QUERY_STOPWORDS, SPECIAL, surface_tokens
from data_sync_etl.domain.query_variants import fold
from data_sync_etl.domain.retrieval_fusion import query_form
from data_sync_etl.domain.search import MAX_QUERY_CHARS, bounded_query

PLANNER_VERSION = 'query-planner-v2'
MAX_VARIANTS = 4
QueryForm = Literal['identifier', 'technical', 'acronym', 'question', 'comparison', 'multi_clause', 'short', 'descriptive']
NEGATION_WORDS = frozenset('không chẳng chớ chưa khong chua not no without never neither nor pas non sans'.split())
COMPARISON_WORDS = frozenset('khác compare comparison versus vs difference different différence comparer'.split())
COMPARISON = re.compile(r'\b(?:so sánh|so sanh|phân biệt|phan biet|khác nhau|khac nhau|compare|versus|difference|différence)\b', re.I)
MANDATORY = re.compile(r'\b(?:phải|bắt buộc|chỉ|must|required|only|both|tous|toutes)\b', re.I)
IDENTIFIER = re.compile(r'(?<!\w)(?:[\w]+(?:[_-][\w]+)+|[A-Za-z]+[0-9₀-₉]+[A-Za-z0-9₀-₉]*|[A-Z][a-z]*(?:[A-Z][A-Za-z0-9]*)+|[A-Z]{2,})(?!\w)')
QUOTED = re.compile(r'"[^"\n]+"|“[^”\n]+”')
# Only anchored scaffolding; retain cách, không, khác, âm and quantity suffixes.
WRAPPERS = tuple(re.compile(pattern, re.I) for pattern in (
    r'^(?:(?:xin|vui lòng|bạn|hãy|giúp tôi)\s+)+',
    r'^(?:cho (?:tôi )?(?:hỏi|biết)|tôi (?:muốn|cần)(?: tìm)?|tìm kiếm|tìm)\s+',
    r'^(?:tài liệu|học liệu|bài học)\s+(?:(?:giải thích|hướng dẫn|chi tiết|về)\s+)+',
    r'^(?:giải thích|trình bày|mô tả)\s+(?:cho tôi (?:biết\s+)?)?',
    r'^(?:please\s+)?(?:help me find|find (?:documents|materials) (?:about|on)|tell me about|explain)\s+',
    r'^(?:veuillez\s+)?(?:expliquez|trouver des documents sur)\s+',
))


class QueryPlan(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    original_query: str = Field(min_length=1, max_length=8000)
    canonical_query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    query_form: QueryForm
    intent: Literal['keyword', 'definition', 'explanation', 'comparison', 'procedure', 'question']
    language_hints: tuple[str, ...] = Field(max_length=3)
    important_terms: tuple[str, ...] = Field(max_length=512)
    technical_identifiers: tuple[str, ...] = Field(max_length=512)
    negation_terms: tuple[str, ...] = Field(max_length=512)
    comparison_constraints: tuple[str, ...] = Field(max_length=512)
    core_query: str = Field(min_length=1, max_length=MAX_QUERY_CHARS)
    core_phrases: tuple[str, ...] = Field(max_length=4)
    decomposed_subqueries: tuple[str, ...] = Field(max_length=4)
    retrieval_variants: tuple[str, ...] = Field(min_length=1, max_length=MAX_VARIANTS)
    decomposition_strategy: Literal['none', 'intent_core', 'clause_split']
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    version: Literal['query-planner-v2'] = PLANNER_VERSION

    @model_validator(mode='after')
    def valid_representations(self) -> Self:
        if bounded_query(self.original_query) != self.canonical_query:
            raise ValueError('Query plan original/canonical mismatch')
        if self.retrieval_variants[0] != self.canonical_query or len(set(self.retrieval_variants)) != len(self.retrieval_variants):
            raise ValueError('Query plan must retain original and deduplicate variants')
        for text in (self.core_query, *self.retrieval_variants, *self.decomposed_subqueries):
            if bounded_query(text) != text:
                raise ValueError('Invalid query representation')
        return self


def detect_technical_identifiers(text: str) -> tuple[str, ...]:
    found = [match.group() for match in SPECIAL.finditer(text)]
    found.extend(match.group() for match in IDENTIFIER.finditer(text))
    found.extend(match.group() for match in QUOTED.finditer(text))
    found.extend(match.group() for match in re.finditer(r'\b(?:[A-ZĐ][a-zà-ỹ]+\s+){1,3}[A-ZĐ][a-zà-ỹ]+\b', text))
    return tuple(dict.fromkeys(found))


def detect_language_hints(text: str) -> tuple[str, ...]:
    words = set(surface_tokens(text))
    hints = []
    # Shared Latin diacritics alone do not establish language.
    if words & {'tôi', 'tìm', 'liệu', 'giải', 'thích', 'của', 'và', 'không', 'cách', 'hợp', 'cư', 'quang'}:
        hints.append('vi')
    if words & {'how', 'does', 'what', 'explain', 'learning', 'database', 'photosynthesis', 'the', 'algorithms'}:
        hints.append('en')
    if words & {'comment', 'fonctionne', 'photosynthèse', 'données', 'pourquoi', 'les', 'une', 'algorithmes'}:
        hints.append('fr')
    return tuple(hints)


def extract_core_query(text: str) -> str:
    result = text
    for _ in range(8):
        previous = result
        for pattern in WRAPPERS:
            result = pattern.sub('', result, count=1).strip()
        if previous == result:
            break
    return result.rstrip(' ?!.') or text


def decompose_clauses(text: str, core: str) -> tuple[str, ...]:
    tokens = surface_tokens(text)
    if (len(tokens) < 10 or set(tokens) & (NEGATION_WORDS | COMPARISON_WORDS) or COMPARISON.search(text)
            or MANDATORY.search(text) or QUOTED.search(text)):
        return ()
    identifiers = detect_technical_identifiers(text)
    parts = [part.strip(' .?!') for part in re.split(r'[,;]|\b(?:đồng thời|kèm theo|cũng như)\b', core)]
    if len(parts) <= 1:
        parts = [part.strip(' .?!') for part in re.split(r'\s+(?:và|and|et)\s+', core)]
    accepted = []
    for part in parts:
        if (len([token for token in surface_tokens(part) if token not in QUERY_STOPWORDS]) >= 2
                and all(term in part for term in identifiers)):
            accepted.append(part)
    distinct = tuple(dict.fromkeys(accepted))[:4]
    return distinct if len(distinct) >= 2 else ()


def plan_query(query: str) -> QueryPlan:
    canonical = bounded_query(query)
    tokens = surface_tokens(canonical)
    negations = tuple(dict.fromkeys(token for token in tokens if token in NEGATION_WORDS))
    comparisons = tuple(dict.fromkeys([*COMPARISON.findall(canonical),
                                     *(token for token in tokens if token in COMPARISON_WORDS)]))
    form = query_form(canonical)
    if comparisons:
        form = 'comparison'
    core = extract_core_query(canonical)
    protected = detect_technical_identifiers(canonical)
    if any(term not in core for term in protected) or any(term not in surface_tokens(core) for term in negations):
        core = canonical
    clauses = decompose_clauses(canonical, core)
    if clauses and form not in ('identifier', 'technical', 'acronym', 'question', 'comparison'):
        form = 'multi_clause'
    intent = 'comparison' if comparisons else 'keyword'
    lower = fold(canonical)
    if not comparisons:
        if re.search(r'\b(?:cach|lam the nao|how to|procedure)\b', lower):
            intent = 'procedure'
        elif re.search(r'\b(?:la gi|dinh nghia|what is|definition)\b', lower):
            intent = 'definition'
        elif re.search(r'\b(?:giai thich|explain|why|tai sao|fonctionne)\b', lower):
            intent = 'explanation'
        elif form == 'question':
            intent = 'question'
    variants = tuple(dict.fromkeys([canonical, *([core] if surface_tokens(core) != tokens and len(surface_tokens(core)) >= 2 else []), *clauses]))[:MAX_VARIANTS]
    payload = dict(original_query=query, canonical_query=canonical, query_form=form, intent=intent,
        language_hints=detect_language_hints(canonical), important_terms=tuple(dict.fromkeys(
            token for token in surface_tokens(core) if token not in QUERY_STOPWORDS or token in negations)),
        technical_identifiers=protected, negation_terms=negations, comparison_constraints=comparisons,
        core_query=core, core_phrases=tuple(dict.fromkeys([core, *clauses]))[:4],
        decomposed_subqueries=clauses, retrieval_variants=variants,
        decomposition_strategy='clause_split' if clauses else 'intent_core' if core != canonical else 'none')
    return QueryPlan(**payload, fingerprint=digest([PLANNER_VERSION, {key: value for key, value in payload.items() if key != 'original_query'}]))


def lexical_plan_variants(plan: QueryPlan, *, decompose: bool = False) -> tuple[str, ...]:
    """Keep original even when a planner strategy is rolled back."""
    values = [plan.canonical_query]
    if surface_tokens(plan.core_query) != surface_tokens(plan.canonical_query) and len(surface_tokens(plan.core_query)) >= 2:
        values.append(plan.core_query)
    if decompose:
        values.extend(plan.decomposed_subqueries)
    return tuple(dict.fromkeys(values))[:MAX_VARIANTS]
