"""Concept-only authority and review attestations. No application login or DB IO."""
import hmac
import json
import re
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from time import time

from data_sync_etl.domain.indexing import digest
from data_sync_etl.ports.concept_corpus import ConceptBlocked

REQUIRED_COVERAGE = frozenset({'resource_visibility', 'version_revision', 'unit_review',
    'unit_retrieval', 'metadata_edit', 'public_scope', 'review_ledger'})


@dataclass(frozen=True)
class AuthorityAttestation:
    revision: str
    coverage: frozenset[str]
    expires_at: float
    complete: bool = False
    available: bool = False

    def valid(self, now):
        return bool(self.available is True and self.complete is True and isinstance(self.revision, str) and self.revision
            and isinstance(self.expires_at, (int, float)) and not isinstance(self.expires_at, bool)
            and isfinite(self.expires_at) and self.expires_at > now
            and REQUIRED_COVERAGE <= self.coverage)


def review_statement(candidate, kind, target, decision):
    return digest([candidate['id'], candidate['binding'], kind, target, decision])


class OfflineReviewAuthority:
    """Explicit synthetic identity, never a verified human or live permission."""
    def verify(self, reviewer, statement, proof=None):
        return {'subject': reviewer, 'method': 'synthetic_identity', 'authenticated': False,
            'scope': 'offline_fixture', 'statement': statement}


class SignedReviewAuthority:
    """Verify an external issuer's decision-bound envelope with an injected key.

    Key distribution/issuer identity is an operator responsibility. No key is
    generated, loaded from env, logged, or installed by composition/CLI here.
    Possession of a caller-supplied key is not independent review acceptance.
    """
    def __init__(self, keys, *, clock=time):
        if not keys or any(not isinstance(key, bytes) or len(key) < 32 for key in keys.values()):
            raise ValueError('Trusted issuer keys of at least 32 bytes required')
        self._keys, self.clock = dict(keys), clock

    def verify(self, reviewer, statement, proof=None):
        try:
            if not isinstance(proof, dict) or set(proof) != {'issuer', 'subject', 'role', 'statement', 'expires', 'nonce', 'signature'}:
                raise ValueError
            body = {key: value for key, value in proof.items() if key != 'signature'}
            key = self._keys[body['issuer']]
            encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
            expected = hmac.new(key, encoded, sha256).hexdigest()
            if (not isinstance(proof['signature'], str) or not hmac.compare_digest(expected, proof['signature'])
                    or body['subject'] != reviewer or body['statement'] != statement
                    or body['role'] != 'concept_reviewer' or not self.clock() < body['expires'] <= self.clock() + 3600
                    or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', body['nonce'])):
                raise ValueError
            return {'subject': reviewer, 'method': 'verified_external_signature', 'authenticated': True,
                'scope': 'offline_fixture', 'statement': statement, 'issuer': body['issuer'],
                'proof_hash': digest(proof)}
        except (ValueError, TypeError, KeyError):
            raise ConceptBlocked('REVIEW_IDENTITY_NOT_VERIFIED') from None
