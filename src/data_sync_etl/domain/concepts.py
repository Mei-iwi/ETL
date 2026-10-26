"""Reviewed concept snapshots and bounded, explainable query expansion.

This dictionary is query-time data, never document TF or embedding input.
"""

import re
from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.domain.indexing import digest

Status = Literal['pending', 'approved', 'rejected']
RelationKind = Literal['equivalent', 'translation', 'broader', 'narrower', 'related']
EXPANSION_VERSION = 'reviewed-context-hints-v2'
_OBSERVATIONS = ContextVar('concept_request_observations', default=None)


@contextmanager
def concept_analysis_scope():
    """Bounded text analysis only; no index state, rankings or ACL decisions."""
    if _OBSERVATIONS.get() is not None:
        yield
        return
    token = _OBSERVATIONS.set({'entries': {}, 'characters': 0})
    try:
        yield
    finally:
        _OBSERVATIONS.reset(token)


def normalize(text):
    from data_sync_etl.domain.lexical import surface_tokens

    return ' '.join(surface_tokens(text))


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Source(Strict):
    name: str = Field(min_length=1)
    identifier: str = Field(min_length=1)


class Term(Strict):
    text: str = Field(min_length=1)
    normalized: str = Field(min_length=1)
    language: str = Field(pattern=r'^[a-z]{2,3}$')
    kind: Literal['preferred', 'synonym', 'translation', 'abbreviation', 'spelling']
    concept_id: str
    domain: str
    context_hints: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    review_status: Status

    @model_validator(mode='after')
    def normalized_text(self):
        if self.normalized != normalize(self.text):
            raise ValueError('Term normalization mismatch')
        return self


class Relation(Strict):
    source_id: str
    target_id: str
    kind: RelationKind
    direction: Literal['outgoing', 'bidirectional'] = 'outgoing'
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    sources: list[Source] = Field(min_length=1)
    review_status: Status
    allow_expansion: bool = False


class Concept(Strict):
    concept_id: str = Field(pattern=r'^[a-zA-Z0-9:_-]+$')
    preferred_labels: dict[str, str] = Field(min_length=1)
    definition: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    terms: list[Term] = Field(min_length=1)
    relations: list[Relation] = Field(default_factory=list)
    sources: list[Source] = Field(min_length=1)
    review_status: Status
    version: str = Field(min_length=1)

    @model_validator(mode='after')
    def membership(self):
        if any(t.concept_id != self.concept_id or t.domain != self.domain for t in self.terms):
            raise ValueError('Term concept/domain mismatch')
        if any(r.source_id != self.concept_id for r in self.relations):
            raise ValueError('Relation source mismatch')
        for language, label in self.preferred_labels.items():
            if not any(t.language == language and t.normalized == normalize(label)
                       and t.kind == 'preferred' for t in self.terms):
                raise ValueError('Preferred label missing from terms')
        return self


class Snapshot(Strict):
    version: str = Field(min_length=1)
    concepts: list[Concept] = Field(min_length=1, max_length=10000)

    @model_validator(mode='after')
    def references(self):
        ids = {c.concept_id for c in self.concepts}
        if len(ids) != len(self.concepts):
            raise ValueError('Duplicate concept ID')
        if any(r.target_id not in ids for c in self.concepts for r in c.relations):
            raise ValueError('Dangling relation')
        return self

    @property
    def fingerprint(self):
        return digest(self.model_dump())


class ExpansionPolicy(Strict):
    enabled: bool = True
    synonyms: float = Field(default=0.8, gt=0, le=1)
    translations: float = Field(default=0.65, gt=0, le=1)
    abbreviations: float = Field(default=0.7, gt=0, le=1)
    relations: float = Field(default=0.35, gt=0, le=1)
    title_boost: float = Field(default=1.0, ge=0, le=3)
    phrase_boost: float = Field(default=1.2, ge=1, le=2)
    min_confidence: float = Field(default=0.8, ge=0, le=1)
    min_sense_score: float = Field(default=2.0, ge=0)
    min_sense_margin: float = Field(default=1.0, ge=0)
    max_per_concept: int = Field(default=12, ge=0, le=50)
    max_total: int = Field(default=64, ge=0, le=200)
    max_depth: int = Field(default=1, ge=0, le=3)
    allowed_relations: list[RelationKind] = Field(default_factory=lambda: ['equivalent', 'translation'])


class ExpandedTerm(Strict):
    query_phrase: str
    term: str
    language: str
    concept_id: str
    query_concept_id: str
    relation: str
    weight: float
    sources: list[Source]
    reason: str


class Recognition(Strict):
    phrase: str
    start: int
    end: int
    concept_id: str | None
    candidates: list[str]
    sense_score: float
    candidate_scores: dict[str, float]
    definition: str | None = None
    domain: str | None = None
    reason: str


class ExpandedQuery(Strict):
    original: str
    dictionary_fingerprint: str
    policy_fingerprint: str
    recognized: list[Recognition]
    terms: list[ExpandedTerm]


@lru_cache(maxsize=1)
def default_dictionary():
    return Dictionary(Snapshot.model_validate_json(
        Path(__file__).with_name('data').joinpath('concepts.json').read_text(encoding='utf-8')))


class Dictionary:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.fingerprint = snapshot.fingerprint
        self.concepts = {c.concept_id: c for c in snapshot.concepts if c.review_status == 'approved'}
        self.aliases = defaultdict(list)
        for concept in self.concepts.values():
            for term in concept.terms:
                if term.review_status == 'approved':
                    self.aliases[term.normalized].append(term)
        # Immutable lookup work belongs to this versioned dictionary, not every
        # query/document mention. Pending terms must never disambiguate a sense.
        self.context_hints = {}
        for cid, concept in self.concepts.items():
            hints = {}
            for term in concept.terms:
                if term.review_status != 'approved':
                    continue
                for hint in term.context_hints:
                    normalized = normalize(hint)
                    if normalized:
                        hints[normalized] = max(hints.get(normalized, 0.), term.confidence)
            self.context_hints[cid] = [(confidence, re.compile(r'(?<!\w)' + re.escape(hint) + r'(?!\w)'))
                                       for hint, confidence in hints.items()]
        def pattern(alias):
            def cjk(c):
                return '\u3040' <= c <= '\u9fff'
            return ('' if cjk(alias[0]) else r'(?<!\w)') + re.escape(alias) + (
                '' if cjk(alias[-1]) else r'(?!\w)')
        self.pattern = re.compile('|'.join(pattern(a) for a in sorted(self.aliases, key=lambda a: (-len(a), a))) or r'(?!)')

    def occurrences(self, text):
        return self.pattern.finditer(normalize(text))

    def observations(self, text, *, policy=None):
        """Do not attribute an ambiguous document mention to the wrong sense."""
        scope = _OBSERVATIONS.get()
        policy = policy or ExpansionPolicy()
        key = (self.fingerprint, policy.model_dump_json(), text)
        if scope is not None and key in scope['entries']:
            yield from scope['entries'][key]
            return
        result = list(self._observations(text, policy=policy))
        if (scope is not None and len(scope['entries']) < 256
                and scope['characters'] + len(text) <= 1_000_000):
            scope['entries'][key] = tuple(result)
            scope['characters'] += len(text)
        yield from result

    def _observations(self, text, *, policy):
        matches = list(self.occurrences(text))
        needs_context = any(len({t.concept_id for t in self.aliases[m.group()]}) > 1
                            or any(t.kind == 'abbreviation' for t in self.aliases[m.group()])
                            for m in matches)
        recognized = {}
        if needs_context:
            policy = (policy or ExpansionPolicy()).model_copy(update={'enabled': False})
            recognized = {(r.start, r.end): r.concept_id
                          for r in self.expand(text, policy=policy).recognized}
        for match in matches:
            ids = {t.concept_id for t in self.aliases[match.group()]}
            contextual = len(ids) > 1 or any(t.kind == 'abbreviation' for t in self.aliases[match.group()])
            cid = recognized.get((match.start(), match.end())) if contextual else next(iter(ids))
            if cid is not None:
                yield match, cid

    def expand(self, query, *, domain=None, policy=None, contextual=True):
        policy = policy or ExpansionPolicy()
        normalized = normalize(query)
        recognized, expanded, seen = [], [], set()
        for match in self.pattern.finditer(normalized):
            terms = [t for t in self.aliases[match.group()] if t.confidence >= policy.min_confidence]
            by_concept = {t.concept_id: t for t in terms}
            if not by_concept:
                continue
            scores = {}
            for cid in by_concept:
                concept = self.concepts[cid]
                # Domain = 3, each distinct context hint outside the mention = 2.
                context = normalized[:match.start()] + ' ' + normalized[match.end():]
                score = (3.0 if domain == concept.domain else 0.0) + 2.0 * sum(
                    bool(pattern.search(context)) for confidence, pattern in self.context_hints[cid]
                    if confidence >= policy.min_confidence)
                scores[cid] = score
            ordered = sorted(scores, key=lambda cid: (-scores[cid], cid))
            cid = ordered[0]
            unique = len(ordered) == 1
            abbreviation = by_concept[cid].kind == 'abbreviation'
            justified = (scores[cid] >= policy.min_sense_score and
                         (unique or scores[cid] - scores[ordered[1]] >= policy.min_sense_margin))
            selected = (unique and not abbreviation) or (contextual and justified)
            reason = ('unique reviewed phrase' if unique and not abbreviation else
                      'domain/context threshold and margin' if selected else 'insufficient context; original retained')
            recognized.append(Recognition(phrase=match.group(), start=match.start(), end=match.end(),
                                          concept_id=cid if selected else None, candidates=ordered,
                                          candidate_scores=scores,
                                          definition=self.concepts[cid].definition if selected else None,
                                          domain=self.concepts[cid].domain if selected else None,
                                          sense_score=scores[cid], reason=reason))
            if not selected or not policy.enabled:
                continue
            source_term = by_concept[cid]
            original = ExpandedTerm(query_phrase=match.group(), term=match.group(),
                language=source_term.language, concept_id=cid, query_concept_id=cid,
                relation='original', weight=1.0, sources=source_term.sources, reason=reason)
            # Originals do not consume expansion budgets; deduplicate repeated mentions.
            key = (cid, cid, original.term, original.language)
            if key not in seen:
                expanded.append(original)
                seen.add(key)
            pending, visited, count = [(cid, 0, 1.0, None, [])], {cid}, 0
            while pending:
                current, depth, path_weight, relation, path_sources = pending.pop(0)
                concept = self.concepts[current]
                for term in concept.terms:
                    key = (cid, current, term.normalized, term.language)
                    if (key in seen or term.review_status != 'approved' or term.confidence < policy.min_confidence
                            or count >= policy.max_per_concept
                            or sum(t.relation != 'original' for t in expanded) >= policy.max_total):
                        continue
                    kind = relation or ('abbreviation' if term.kind == 'abbreviation' else
                                        'translation' if term.language != source_term.language else 'synonym')
                    weight = (policy.abbreviations if kind == 'abbreviation' else
                              policy.translations if kind == 'translation' else policy.synonyms)
                    expanded.append(ExpandedTerm(query_phrase=match.group(), term=term.normalized,
                        language=term.language, concept_id=current, query_concept_id=cid, relation=kind,
                        weight=path_weight * weight * term.confidence,
                        sources=[*term.sources, *path_sources], reason=reason if depth == 0 else f'reviewed {relation} relation'))
                    seen.add(key)
                    count += 1
                if depth >= policy.max_depth:
                    continue
                # Incoming traversal only when the reviewed edge is explicitly bidirectional.
                edges = [(r, r.target_id) for r in concept.relations]
                edges += [(r, c.concept_id) for c in self.concepts.values() for r in c.relations
                          if r.target_id == current and r.direction == 'bidirectional']
                for edge, target in edges:
                    if (edge.review_status == 'approved' and edge.allow_expansion
                            and edge.kind in policy.allowed_relations and edge.confidence >= policy.min_confidence
                            and target in self.concepts and target not in visited):
                        visited.add(target)
                        pending.append((target, depth + 1, path_weight * policy.relations * edge.confidence,
                                        edge.kind, [*path_sources, *edge.sources]))
        # If multiple query concepts reach the same evidence, keep its strongest
        # path once. Direct mentions outrank weaker related-concept paths.
        unique_terms = {}
        for term in expanded:
            key = (term.concept_id, term.term, term.language)
            old = unique_terms.get(key)
            if old is None or term.weight > old.weight or (
                    term.weight == old.weight and term.relation == 'original'):
                unique_terms[key] = term
        return ExpandedQuery(original=query, dictionary_fingerprint=self.fingerprint,
                             policy_fingerprint=digest([EXPANSION_VERSION, policy.model_dump()]), recognized=recognized,
                             terms=list(unique_terms.values()))

    def evidence(self, expansion, text, *, field='content', policy=None):
        """Only actual source phrases, preserving source casing and punctuation."""
        import unicodedata

        from data_sync_etl.domain.lexical import QUERY_STOPWORDS, TOKEN, surface_tokens

        source = unicodedata.normalize('NFC', text)
        offsets = []
        for token in TOKEN.finditer(source):
            if offsets:
                offsets.append(token.start())  # normalized separating space
            # casefold may expand characters (e.g. German sharp s).
            for index, character in enumerate(token.group()):
                offsets.extend([token.start() + index] * len(character.casefold()))
        result = []
        for match, cid in self.observations(text, policy=policy):
            for term in expansion.terms:
                if match.group() == term.term and cid == term.concept_id:
                    begin, end = offsets[match.start()], offsets[match.end() - 1] + 1
                    result.append({**term.model_dump(), 'matched_phrase': source[begin:end],
                                   'field': field, 'start': begin, 'end': end})
        remaining = set(surface_tokens(expansion.original)) - QUERY_STOPWORDS
        for term in expansion.terms:
            if term.relation == 'original':
                remaining.difference_update(surface_tokens(term.query_phrase))
        for token in TOKEN.finditer(source):
            if token.group().casefold() in remaining:
                result.append(dict(query_phrase=token.group().casefold(), term=token.group().casefold(),
                    matched_phrase=token.group(), field=field, start=token.start(), end=token.end(),
                    concept_id=None, query_concept_id=None, language='und', relation='original',
                    weight=1.0, sources=[], reason='literal surface token'))
        return list({(r['query_concept_id'], r['matched_phrase'], r['field']): r for r in result}.values())


def concept_matches(tokens):
    """Reviewed source concepts for passage ranking; longest match wins."""
    dictionary = default_dictionary()
    expansion = dictionary.expand(' '.join(tokens))
    return [(dictionary.concepts[m.concept_id].preferred_labels.get('en', m.concept_id), m.phrase)
            for m in expansion.recognized if m.concept_id]


def concept_frequencies(tokens):
    return Counter(concept for concept, _ in concept_matches(tokens))


def equivalent_terms(query, tokens, *, expansion=None, dictionary=None, policy=None):
    dictionary = dictionary or default_dictionary()
    expansion = expansion or dictionary.expand(query)
    return {word for row in dictionary.evidence(expansion, ' '.join(tokens), policy=policy)
            for word in row['matched_phrase'].casefold().split()}
