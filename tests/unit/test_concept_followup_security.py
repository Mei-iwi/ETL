"""Checkpoint 02: authority/reviewer/outbox fixtures, never live acceptance."""
import hmac
import json
from dataclasses import replace
from hashlib import sha256

import pytest

from data_sync_etl.application.concept_discovery import ReviewLedger
from data_sync_etl.domain.concept_authority import (
    REQUIRED_COVERAGE,
    AuthorityAttestation,
    SignedReviewAuthority,
    review_statement,
)
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.corpus_concepts import EligibilityFence
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from tests.unit.test_dynamic_concept_dictionary import activate, approve, resource
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline


@pytest.mark.parametrize('missing', sorted(REQUIRED_COVERAGE))
def test_revocation_requires_complete_attested_coverage(pipeline, missing):
    inventory, _, _, _ = pipeline
    inventory.attestation = lambda: AuthorityAttestation(inventory.token(), REQUIRED_COVERAGE - {missing},
        expires_at=200, complete=True, available=True)
    fence = EligibilityFence(inventory, clock=lambda: 100)
    with pytest.raises(ConceptBlocked, match='PRIVACY'):
        fence.capture()
    assert fence.unchanged(inventory.token()) is False


@pytest.mark.parametrize('change', [dict(fields={'title': 'changed'}), dict(deleted=True), dict(public=False),
    dict(approved_unit_ids=frozenset({'changed'})), dict(metadata_approved=False), dict(lifecycle='REJECTED')])
def test_revoke_after_pin_and_restart_never_returns_cached_alias(pipeline, change):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    with pytest.raises(ConceptBlocked, match='DYNAMIC_REVOKED'), provider.pin('cache'):
        inventory.put(replace(resource(), revision=2, **change))
    with provider.pin('cache') as dictionary:
        assert dictionary is default_dictionary()
    with pytest.raises(ConceptBlocked, match='ROLLBACK'):
        store.rollback(store.current(), fence=worker.fence.unchanged)


def test_review_identity_default_deny_and_signed_decision_binding(pipeline):
    _, store, _, candidate = pipeline
    args = dict(kind='relation', target=candidate['id'], decision='approved', reviewer='test-reviewer')
    with pytest.raises(ConceptBlocked, match='IDENTITY'):
        ReviewLedger(store).decide(candidate, **args)
    key = b'synthetic-test-issuer-key-32-bytes-only'
    authority = SignedReviewAuthority({'test-issuer': key}, clock=lambda: 100)
    body = dict(issuer='test-issuer', subject='test-reviewer', role='concept_reviewer',
        statement=review_statement(candidate, 'relation', candidate['id'], 'approved'), expires=200, nonce='syntheticnonce123')

    def signed(value):
        return {**value, 'signature': hmac.new(key, json.dumps(value, sort_keys=True,
            separators=(',', ':'), ensure_ascii=True).encode(), sha256).hexdigest()}

    ledger = ReviewLedger(store, authority=authority)
    for altered in ({**body, 'role': 'reader'}, {**body, 'expires': 99}, {**body, 'statement': 'stale-candidate'}):
        with pytest.raises(ConceptBlocked, match='IDENTITY'):
            ledger.decide(candidate, **args, proof=signed(altered))
    proof = signed(body)
    with pytest.raises(ConceptBlocked, match='IDENTITY'):
        ledger.decide(candidate, **args, proof={**proof, 'signature': '0' * 64})
    ledger.decide(candidate, **args, proof=proof)
    with pytest.raises(ConceptBlocked, match='REPLAY'):
        ledger.decide(candidate, **args, proof=proof)
    record = json.loads(store.path('review/decisions.jsonl').read_text(encoding='utf-8'))
    assert record['identity']['authenticated'] is True
    assert record['scope'] == 'offline_fixture'
    assert 'signature' not in record['identity']


def test_global_priority_revoke_outside_batch_survives_restart_and_pressure(pipeline):
    from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
    inventory, store, worker, _ = pipeline
    for number in range(2, 20):
        inventory.put(resource(number))
        worker.events.enqueue(resource(number), 'add')
    revoked = resource(99, revision=2, public=False)
    inventory.put(revoked)
    worker.events.enqueue(revoked, 'revoke')
    restarted = ConceptDiscoveryWorker(inventory, store, batch_size=1, pressure=lambda: True)
    assert restarted.events.peek(1)[0]['resource_id'] == revoked.resource_id
    restarted.tick()
    assert store.read_json(worker.contribution_key(revoked.resource_id))['relations'] == []
    assert all(item['resource_id'] != revoked.resource_id for item in restarted.events.peek(100))
    assert restarted.status()['urgent_revocations'] == 1
    assert restarted.events.enqueue(resource(99, revision=1), 'add') is True  # stale versus queue, checked against authority at processing


def test_review_attestation_expiry_and_authority_outage_fail_closed(pipeline):
    inventory, _, _, _ = pipeline
    inventory.attestation = lambda: AuthorityAttestation(inventory.token(), REQUIRED_COVERAGE,
        expires_at=110, complete=True, available=True)
    now = [100]
    fence = EligibilityFence(inventory, clock=lambda: now[0])
    token = fence.capture()
    now[0] = 111
    assert not fence.unchanged(token)
    inventory.attestation = lambda: (_ for _ in ()).throw(OSError('synthetic unavailable'))
    assert not fence.unchanged(token)


def test_disabling_dynamic_flag_mid_request_revokes_pinned_alias(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    try:
        with pytest.raises(ConceptBlocked, match='DYNAMIC_REVOKED'), provider.pin('cache'):
            provider.enabled = False
        with provider.pin('cache') as dictionary:
            assert dictionary is default_dictionary()
    finally:
        provider.close()


def test_attestation_does_not_trust_truthy_flags_or_unbounded_expiry():
    base = AuthorityAttestation('synthetic-revision', REQUIRED_COVERAGE, expires_at=200, complete=True, available=True)
    assert base.valid(100)
    for altered in (replace(base, complete='false'), replace(base, available=1),
                    replace(base, expires_at=float('inf')), replace(base, expires_at=True)):
        assert not altered.valid(100)
