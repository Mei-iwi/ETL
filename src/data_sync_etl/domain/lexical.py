"""Versioned full-content lexical analysis and bag-level BM25."""

import math
import re
import unicodedata
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.domain.indexing import digest

ANALYZER_VERSION = 'underthesea-8.3.0-nfc-surface-compound-alnum-v2'
REPRESENTATION_VERSION = 'bag-lexical-tf-v1'
RANKING_VERSION = 'bm25-concept-dismax-v3'
TOKEN = re.compile(r'c\+\+|c#|\.net\b|\d+(?:\.\d+)+|[^\W_]+', re.IGNORECASE)
SPECIAL = re.compile(r'(c\+\+|c#|\.net\b)', re.IGNORECASE)
_ANALYSIS = ContextVar('lexical_request_analysis', default=None)


@contextmanager
def analysis_scope():
    """Reuse exact-text analysis within one request; never cache index/ACL state."""
    if _ANALYSIS.get() is not None:
        yield
        return
    token = _ANALYSIS.set({'tokens': {}, 'characters': 0})
    try:
        yield
    finally:
        _ANALYSIS.reset(token)


QUERY_STOPWORDS = frozenset(
    'và là của có các những một được trong cho với về từ đến này đó khi thì mà hoặc đã đang sẽ '
    'the a an of and to in is are for on what how why where when which who whom whose '
    'does do can could would should please explain about this that these those it its '
    'as be been being'.split())


def surface_tokens(text):
    return TOKEN.findall(unicodedata.normalize('NFC', text).casefold())


def segmentation_windows(text):
    """Bound CRF working memory; surface/phrase coverage remains complete."""
    start = 0
    for ordinal, match in enumerate(re.finditer(r'\S+\s*', text), 1):
        if ordinal % 256 == 0:
            yield text[start:match.end()]
            start = match.end()
    if start < len(text):
        yield text[start:]


def analyze(text):
    """Retain all surface words/numbers and learned Vietnamese compounds.

    Surface words ensure segmentation at query boundaries cannot hide a match.
    Stopwords remain available for phrases/negation. No accent folding or
    unbounded ngrams. Technical symbols bypass punctuation splitting.
    """
    scope = _ANALYSIS.get()
    if scope is not None and text in scope['tokens']:
        return list(scope['tokens'][text])
    result = _analyze(text)
    # Bound request-local retention even for very large corpora. Copies prevent
    # callers from modifying later analyses of the same text.
    if (scope is not None and len(scope['tokens']) < 4096
            and scope['characters'] + len(text) + sum(map(len, result)) <= 2_000_000):
        scope['tokens'][text] = tuple(result)
        scope['characters'] += len(text) + sum(map(len, result))
    return result


def _analyze(text):
    from data_sync_etl.domain.indexing import IndexingError

    try:
        from importlib.metadata import version

        from underthesea import word_tokenize

        if version('underthesea') != '8.3.0':
            raise RuntimeError('Unsupported Underthesea version')
    except (ImportError, RuntimeError) as exc:
        raise IndexingError('BLOCKED_LEXICAL_ANALYZER', 503) from exc

    result = []
    for part in SPECIAL.split(unicodedata.normalize('NFC', text)):
        if SPECIAL.fullmatch(part):
            result.append(part.casefold())
        else:
            surface = surface_tokens(part)
            result.extend(surface)
            # Identifiers stay whole; do not invent "word" evidence in word42.
            for window in segmentation_windows(part):
                try:
                    segmented = word_tokenize(window, format='text')
                except Exception as exc:
                    raise IndexingError('BLOCKED_LEXICAL_ANALYZER', 503) from exc
                result.extend(token.casefold() for token in segmented.split() if '_' in token)
    return result


def query_terms(text):
    # Keep stopwords in the stored surface sequences for exact phrases. Only
    # abstain on queries made entirely of function words; retain negation.
    surface = surface_tokens(text)
    return analyze(text) if any(term not in QUERY_STOPWORDS for term in surface) else []


class LexicalDocument(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    version: str = REPRESENTATION_VERSION
    analyzer: str = ANALYZER_VERSION
    fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    terms: list[str]
    frequencies: list[int]
    length: int = Field(ge=0)
    title_terms: list[str]
    title_frequencies: list[int]
    # Surface sequence preserves stopwords and phrase order; boundaries between
    # descendant leaves remain separate to avoid inventing cross-leaf phrases.
    passages: list[list[str]]
    title_tokens: list[str]

    @model_validator(mode='after')
    def consistent(self):
        for terms, frequencies in ((self.terms, self.frequencies),
                                   (self.title_terms, self.title_frequencies)):
            if (terms != sorted(set(terms)) or len(terms) != len(frequencies)
                    or any(type(n) is not int or n < 1 for n in frequencies)):
                raise ValueError('Invalid lexical term frequencies')
        if self.length != sum(self.frequencies):
            raise ValueError('Invalid lexical document length')
        return self


def lexical_document(texts, title=None):
    texts = [unicodedata.normalize('NFC', text) for text in texts]
    title = unicodedata.normalize('NFC', title or '')
    # Surface words are the scoring channel; compound tokens remain available
    # from analyze() for consumers, but never inflate TF or length here.
    for text in [*texts, title]:
        analyze(text)  # Require the pinned tokenizer; no silent fallback.
    tf = Counter(token for text in texts for token in surface_tokens(text))
    title_tf = Counter(surface_tokens(title))
    tf.update(title_tf)
    terms, title_terms = sorted(tf), sorted(title_tf)
    return LexicalDocument(
        version=REPRESENTATION_VERSION, analyzer=ANALYZER_VERSION,
        fingerprint=digest([REPRESENTATION_VERSION, ANALYZER_VERSION, title, texts]),
        terms=terms, frequencies=[tf[t] for t in terms], length=sum(tf.values()),
        title_terms=title_terms, title_frequencies=[title_tf[t] for t in title_terms],
        passages=[surface_tokens(text) for text in texts], title_tokens=surface_tokens(title),
    )


def contains_phrase(tokens, query):
    if not query:
        return False
    return any(tokens[i:i + len(query)] == query for i in range(len(tokens) - len(query) + 1))


def bm25(query, documents, *, k1=1.2, b=0.75, expand_concepts=True,
         dictionary=None, policy=None, domain=None, contextual=True, expansion=None):
    """Weighted BM25, maximum alternative per concept (no alias stacking).

    N, avgdl and DF cover ALL authorized verified bags including nonhits.
    Each recognized query concept replaces its constituent word features.
    Each alias is a phrase feature; concept DF is the union of its alternatives.
    The strongest weighted alias contributes once per concept. Literal leftover
    surface words are unique query features. Title contributes an extra TF boost.
    """
    from data_sync_etl.domain.concepts import ExpansionPolicy, default_dictionary

    if not documents:
        return {}
    policy = policy or ExpansionPolicy()
    dictionary = dictionary or default_dictionary()
    expansion = expansion or dictionary.expand(query, domain=domain, policy=policy, contextual=contextual)
    terms = {t for t in surface_tokens(query) if t not in QUERY_STOPWORDS}
    if not query_terms(query):
        return {}
    groups = {}
    if expand_concepts and policy.enabled:
        for term in expansion.terms:
            groups.setdefault(term.query_concept_id, []).append(term)
        for mention in expansion.recognized:
            if mention.concept_id in groups:
                terms.difference_update(surface_tokens(mention.phrase))
    n = len(documents)
    avgdl = sum(doc.length for doc in documents.values()) / n or 1
    df = Counter(term for doc in documents.values() for term in doc.terms)
    observed = {}
    for key, doc in documents.items():
        # Longest dictionary matches prevent a qualified phrase from becoming
        # evidence for a shorter, different sense. Passage boundaries are kept.
        counts, title = Counter(), Counter()
        if groups:
            title.update((cid, m.group()) for m, cid in dictionary.observations(' '.join(doc.title_tokens), policy=policy))
            counts.update(title)
            for passage in doc.passages:
                counts.update((cid, m.group()) for m, cid in dictionary.observations(' '.join(passage), policy=policy))
        observed[key] = counts, title
    concept_df = {cid: sum(any(counts[t.concept_id, t.term] for t in alternatives)
                          for counts, _ in observed.values()) for cid, alternatives in groups.items()}
    def contribution(freq, document_frequency, length):
        idf = math.log1p((n - document_frequency + 0.5) / (document_frequency + 0.5))
        return idf * freq * (k1 + 1) / (freq + k1 * (1 - b + b * length / avgdl))
    result = {}
    exact = surface_tokens(query)
    for period in range(1, len(exact) // 2 + 1):
        if len(exact) % period == 0 and exact == exact[:period] * (len(exact) // period):
            exact = exact[:period]
            break
    for key, doc in documents.items():
        tf = dict(zip(doc.terms, doc.frequencies, strict=True))
        title_tf = dict(zip(doc.title_terms, doc.title_frequencies, strict=True))
        score = sum(contribution(tf[t] + policy.title_boost * title_tf.get(t, 0), df[t], doc.length)
                    for t in sorted(terms & tf.keys()))
        counts, title = observed[key]
        for cid, alternatives in groups.items():
            score += max((t.weight * contribution(counts[t.concept_id, t.term] + policy.title_boost * title[t.concept_id, t.term],
                                                 concept_df[cid], doc.length)
                          for t in alternatives if counts[t.concept_id, t.term]), default=0.0)
        if score > 0:
            if len(set(exact)) > 1 and any(contains_phrase(tokens, exact)
                                         for tokens in [doc.title_tokens, *doc.passages]):
                score *= policy.phrase_boost
            result[key] = score
    return result


def snippet_text(text, terms, size=400, *, query=None, anchor=None, expansion=None, dictionary=None, policy=None):
    """Center a snippet on a surface hit instead of always showing its prefix."""
    from data_sync_etl.domain.context_ranking import content_terms

    folded = unicodedata.normalize('NFC', text)
    if anchor is not None:
        start = max(0, min(len(folded), anchor) - size // 4)
        return folded[start:start + size]
    matches = list(TOKEN.finditer(folded))
    terms = content_terms(terms)
    if query:
        from data_sync_etl.domain.concepts import equivalent_terms

        terms = set(terms) | equivalent_terms(query, surface_tokens(text), expansion=expansion, dictionary=dictionary, policy=policy)
    hits = []
    for match in matches:
        token = match.group().casefold()
        found = ({token} | set(re.findall(r'[^\W\d_]+', token))) & terms
        found.update(term for term in terms if any('\u3040' <= char <= '\u9fff' for char in term)
                     and term in token)
        hits.extend((term, match.start() + max(0, token.find(term))) for term in sorted(found))
    counts = Counter(term for term, _ in hits)
    positions = [position for term, position in sorted(hits, key=lambda hit: (counts[hit[0]], hit[1]))]
    exact = surface_tokens(query or '')
    tokens = [match.group().casefold() for match in matches]
    if len(exact) > 1:
        phrase_start = next((matches[i].start() for i in range(len(tokens) - len(exact) + 1)
                             if tokens[i:i + len(exact)] == exact), None)
        if phrase_start is not None:
            positions = [phrase_start]
    start = max(0, (positions[0] if positions else 0) - size // 4)
    return folded[start:start + size]
