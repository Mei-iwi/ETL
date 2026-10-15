"""Deterministic word windows and TF, independent of semantic embeddings."""

import json
import math
import re
import unicodedata
from collections import Counter
from hashlib import sha256

from data_sync_etl.domain.core import DomainError


class IndexingError(DomainError):
    def __init__(self, code: str, status: int = 409):
        self.code = code
        self.status = status
        super().__init__(code)


def digest(value) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def word_windows(text: str, size: int, overlap: int):
    if not 0 <= overlap < size:
        raise ValueError("Chunk overlap must be smaller than chunk size")
    spans = list(re.finditer(r"\S+", text))
    for offset in range(0, len(spans), size - overlap):
        end = min(offset + size, len(spans))
        start_char, end_char = spans[offset].start(), spans[end - 1].end()
        yield start_char, end_char, text[start_char:end_char]
        if end == len(spans):
            break


def bag_of_words(text: str):
    normalized = unicodedata.normalize("NFC", text).casefold()
    terms = Counter(re.findall(r"[^\W_]+", normalized, re.UNICODE))
    return dict(sorted(terms.items()))


def validate_vector(vector, dimension: int):
    if (len(vector) != dimension or not vector or
            any(isinstance(v, bool) or not isinstance(v, (float, int))
                or not math.isfinite(v) for v in vector) or not any(vector)):
        raise IndexingError("INVALID_EMBEDDING", 503)
    return [float(v) for v in vector]
