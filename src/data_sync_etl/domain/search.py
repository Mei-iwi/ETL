"""Dense cosine/RRF and the legacy binary scorer retained for comparisons.

Production lexical ranking lives in domain.lexical (BM25).
"""

import math
import unicodedata

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.indexing import validate_vector


class SearchError(DomainError):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def normalize_query(value):
    value = unicodedata.normalize('NFC', value)
    if any(unicodedata.category(char) in ('Cc', 'Cs') and char not in '\t\r\n' for char in value):
        raise ValueError('Invalid search query control character')
    return ' '.join(value.split())


MAX_QUERY_CHARS = 4000
MAX_QUERY_TOKENS = 512


def bounded_query(value):
    """Preserve full intent, identifiers and negation within explicit budgets."""
    from data_sync_etl.domain.lexical import surface_tokens

    if not isinstance(value, str) or len(value) > 8000:
        raise ValueError('Invalid search query length')
    value = normalize_query(value)
    if not 1 <= len(value) <= MAX_QUERY_CHARS or len(surface_tokens(value)) > MAX_QUERY_TOKENS:
        raise ValueError('Invalid search query budget')
    return value


def binary_score(query, keywords):
    left, right = set(query), set(keywords)
    matched = sorted(left & right)
    return (len(matched) / math.sqrt(len(left) * len(right)) if left and right else 0.0), matched


def cosine(left, right):
    left = validate_vector(left, len(left))
    right = validate_vector(right, len(left))
    # Scale before norm/dot to avoid overflow with otherwise finite vectors.
    a, b = max(map(abs, left)), max(map(abs, right))
    left, right = [v / a for v in left], [v / b for v in right]
    value = math.fsum(x * y for x, y in zip(left, right, strict=True))
    norm = math.sqrt(math.fsum(x*x for x in left) * math.fsum(y*y for y in right))
    return max(-1.0, min(1.0, value / norm))


def cosine_many(query, vectors):
    """Same bounded cosine contract, validating/normalizing the query once.

    No cross-request vector cache. math.sumprod runs the dot products in C on
    Python 3.12+, and scaling still protects finite extreme input magnitudes.
    """
    query = validate_vector(query, len(query))
    scale = max(map(abs, query))
    left = [value / scale for value in query]
    left_norm = math.sqrt(math.sumprod(left, left))
    result = []
    for candidate in vectors:
        right = validate_vector(candidate, len(left))
        scale = max(map(abs, right))
        right = [value / scale for value in right]
        denominator = left_norm * math.sqrt(math.sumprod(right, right))
        result.append(max(-1., min(1., math.sumprod(left, right) / denominator)))
    return result


def ranks(scores):
    return {key: rank for rank, (key, _) in enumerate(
        sorted(scores.items(), key=lambda item: (-item[1], item[0])), 1)}


def rrf(*branches):
    result = {}
    for branch in branches:
        for key, rank in ranks(branch).items():
            result[key] = result.get(key, 0.0) + 1.0 / (60 + rank)
    return result
