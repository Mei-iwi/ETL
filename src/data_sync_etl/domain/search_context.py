"""URL-safe locators, not credentials. Every field is revalidated against current data."""

import base64
import binascii
import json
import re

from data_sync_etl.domain.search import SearchError


def encode_context(version_id, unit_id, ordinal, fingerprint):
    value = json.dumps([version_id, unit_id, ordinal, fingerprint[:32]], separators=(',', ':'))
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip('=')


def decode_context(token):
    try:
        if not isinstance(token, str) or not 1 <= len(token) <= 256 or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
            raise ValueError
        values = json.loads(base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True))
        if not isinstance(values, list) or len(values) != 4:
            raise ValueError
        version, unit, ordinal, fingerprint = values
        if (any(not isinstance(key, str) or not 1 <= len(key) <= 30 or key != key.strip() for key in (version, unit))
                or type(ordinal) is not int or not 1 <= ordinal <= 10000
                or not isinstance(fingerprint, str) or not re.fullmatch('[a-f0-9]{32}', fingerprint)
                or encode_context(version, unit, ordinal, fingerprint) != token):
            raise ValueError
        return version, unit, ordinal, fingerprint
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        raise SearchError('CONTEXT_NOT_FOUND', 404) from None
