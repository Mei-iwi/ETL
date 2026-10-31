"""Canonical SOURCE publication/lifecycle status handling.

SOURCE status values are external data.  Unknown values must fail closed rather
than being treated as an approved document, and publication status must not be
silently conflated with processing state.
"""

from collections.abc import Iterable

_ALIASES = {
    'APPROVED': 'APPROVED',
    'PUBLISHED': 'PUBLISHED',
    'UNREVIEWED': 'UNREVIEWED',
    'PRIVATE': 'PRIVATE',
    'REJECTED': 'REJECTED',
    'DELETED': 'DELETED',
    'ARCHIVED': 'DELETED',
}

_BLOCKED = frozenset({'PRIVATE', 'REJECTED', 'DELETED'})


def normalize_source_status(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().upper().replace('-', '_').replace(' ', '_')
    return _ALIASES.get(key)


def source_status_allowed(value: object, allowed: Iterable[object]) -> bool:
    status = normalize_source_status(value)
    if status is None or status in _BLOCKED:
        return False
    return status in {normalized for item in allowed
                      if (normalized := normalize_source_status(item)) is not None}
