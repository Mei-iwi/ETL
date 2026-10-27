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
    def verify(self, reviewer, statement, proof=None, *, context=None):
        return {'subject': reviewer, 'method': 'synthetic_identity', 'authenticated': False,
            'scope': 'offline_fixture', 'statement': statement}


@dataclass(frozen=True)
class ReviewerKeyPolicy:
    subjects: frozenset[str]
    domains: frozenset[str]
    actions: frozenset[str]
    review_kinds: frozenset[str]
    expires_at: float


REVIEW_VERSION = 'concept-review-v2'


class SignedReviewAuthority:
    """Verify an external issuer's decision-bound envelope with an injected key.

    Key distribution/issuer identity is an operator responsibility. No key is
    generated, loaded from env, logged, or installed by composition/CLI here.
    Possession of a caller-supplied key is not independent review acceptance.
    """
    require_authenticated = True

    def __init__(self, keys, *, policies=None, clock=time):
        if not keys or any(not isinstance(key, bytes) or len(key) < 32 for key in keys.values()):
            raise ValueError('Trusted issuer keys of at least 32 bytes required')
        self._keys, self.clock = dict(keys), clock
        self._policies, self._revoked = dict(policies or {}), set()

    def revoke_key(self, key_id):
        self._revoked.add(key_id)

    def token(self):
        return digest([sorted(self._revoked), [(key, sorted(policy.subjects), sorted(policy.domains),
            sorted(policy.actions), sorted(policy.review_kinds), policy.expires_at, self.clock() < policy.expires_at)
            for key, policy in sorted(self._policies.items())]])

    def receipt_valid(self, receipt):
        policy = self._policies.get(receipt.get('issuer'))
        return bool(policy and receipt.get('issuer') not in self._revoked
            and receipt.get('subject') in policy.subjects and receipt.get('domain') in policy.domains
            and receipt.get('action') in policy.actions and receipt.get('review_kind') in policy.review_kinds
            and self.clock() < min(receipt.get('expires', 0), policy.expires_at))

    def verify(self, reviewer, statement, proof=None, *, context=None):
        try:
            if not isinstance(proof, dict) or set(proof) != {'issuer', 'subject', 'role', 'statement', 'expires',
                    'nonce', 'signature', 'issued_at', 'scope', 'version'}:
                raise ValueError
            body = {key: value for key, value in proof.items() if key != 'signature'}
            key = self._keys[body['issuer']]
            policy = self._policies[body['issuer']]
            if not context or body['scope'] != context or body['version'] != REVIEW_VERSION:
                raise ValueError
            encoded = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()
            if len(encoded) > 4096 or any(type(body[name]) not in (int, float) or not isfinite(body[name])
                    for name in ('issued_at', 'expires')):
                raise ValueError
            expected = hmac.new(key, encoded, sha256).hexdigest()
            if (not isinstance(proof['signature'], str) or not hmac.compare_digest(expected, proof['signature'])
                    or body['subject'] != reviewer or body['statement'] != statement
                    or body['role'] != 'concept_reviewer' or not body['issued_at'] <= self.clock() < body['expires'] <= self.clock() + 3600
                    or not re.fullmatch(r'[A-Za-z0-9_-]{16,80}', body['nonce'])):
                raise ValueError
            receipt = {'subject': reviewer, 'method': 'verified_external_signature', 'authenticated': True,
                'scope': 'offline_fixture', 'statement': statement, 'issuer': body['issuer'],
                'proof_hash': digest(proof), 'nonce_hash': digest([body['issuer'], body['nonce']]),
                'domain': context['domain'], 'review_kind': context['kind'], 'action': context['action'],
                'expires': min(body['expires'], policy.expires_at)}
            if not self.receipt_valid(receipt):
                raise ValueError
            return receipt
        except (ValueError, TypeError, KeyError):
            raise ConceptBlocked('REVIEW_IDENTITY_NOT_VERIFIED') from None
