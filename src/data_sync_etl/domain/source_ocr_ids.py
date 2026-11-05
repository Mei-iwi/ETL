"""Deterministic SOURCE OCR business identities, independent of persistence."""

import json
from hashlib import sha256


def stable_id(kind, *parts):
    return kind + sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()[:28]


def source_owned_id(value, kind):
    return (isinstance(value, str) and len(value) == 30 and value.startswith(kind)
            and all(character in '0123456789abcdef' for character in value[2:]))
