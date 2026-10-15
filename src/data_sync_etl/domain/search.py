"""Read-only Bag ranking; binary keywords, cosine and reciprocal rank fusion."""

import math
import unicodedata

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.indexing import validate_vector


class SearchError(DomainError):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def normalize_query(value):
    return unicodedata.normalize('NFC', value).strip()


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


def ranks(scores):
    return {key: rank for rank, (key, _) in enumerate(
        sorted(scores.items(), key=lambda item: (-item[1], item[0])), 1)}


def rrf(*branches):
    result = {}
    for branch in branches:
        for key, rank in ranks(branch).items():
            result[key] = result.get(key, 0.0) + 1.0 / (60 + rank)
    return result
