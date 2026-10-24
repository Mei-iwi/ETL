"""Rank source passages using explicit query evidence, without model inference."""

import math
from collections import Counter

from data_sync_etl.domain.concepts import concept_frequencies
from data_sync_etl.domain.lexical import QUERY_STOPWORDS, contains_phrase, surface_tokens

CONTEXT_RANKING_VERSION = 'query-surface-compound-v2'

FUNCTION_WORDS = QUERY_STOPWORDS | frozenset(
    'what how why which where who explain please about does do can could would '
    'tôi mình bạn hãy giải thích tại sao như thế nào'.split())


def content_terms(tokens):
    return {term for term in tokens if any(word not in FUNCTION_WORDS for word in term.split('_'))}


class ContextRanker:
    """Exact phrases, equivalent concepts, then informative word coverage.

    Statistics cover authorized chunks only. Function words cannot select an
    otherwise unrelated passage. Stable ties retain source order. A zero score
    means the passage is a representative fallback, not query evidence.
    """

    def __init__(self, query, keywords, texts, *, checkpoint=lambda: None, expand_concepts=True,
                 expansion=None, dictionary=None, policy=None):
        self.exact = surface_tokens(query or '')
        self.terms = content_terms(keywords)
        compounds = {term: surface_tokens(term.replace('_', ' '))
                     for term in self.terms if '_' in term}
        # An explicit expansion already drives evidence scoring below. Avoid a
        # second default-dictionary expansion whose result that branch never uses.
        self.concepts = (set(concept_frequencies(self.exact))
                         if expand_concepts and not (dictionary is not None and expansion is not None) else set())
        self.expansion, self.dictionary = expansion, dictionary
        self.policy = policy
        self.tokens, self.words = [], []
        for text in texts:
            checkpoint()
            tokens = surface_tokens(text)
            self.tokens.append(tokens)
            # Query terms already carry the pinned tokenizer's compounds. Match
            # their source word sequences directly, instead of running CRF on
            # every passage and discarding all unrelated compound tokens.
            words = content_terms(tokens)
            words.update(term for term, phrase in compounds.items() if contains_phrase(tokens, phrase))
            self.words.append(words)
        df = Counter(term for words in self.words for term in words)
        self.weights = {term: math.log1p((len(texts) + 1) / (df[term] + 1)) for term in self.terms}

    def score(self, ordinal):
        tokens, words = self.tokens[ordinal], self.words[ordinal]
        exact = bool(self.terms and contains_phrase(tokens, self.exact))
        concepts = (len(self.dictionary.evidence(self.expansion, ' '.join(tokens), policy=self.policy))
                    if self.dictionary is not None and self.expansion is not None
                    else len(self.concepts & concept_frequencies(tokens).keys()))
        matched = self.terms & words
        coverage = sum(self.weights[term] for term in sorted(matched))
        # Prefer a concise passage when query evidence is otherwise identical.
        density = len(matched) / max(1, len(tokens)) if matched else 0.0
        return int(exact), concepts, coverage, density
