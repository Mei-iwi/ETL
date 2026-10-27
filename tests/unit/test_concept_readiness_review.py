"""P03 issuer tests use TEST_ONLY keys, never independent reviewer evidence."""
import hmac
import json
from hashlib import sha256

import pytest

from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker, term_target
from data_sync_etl.domain.concept_authority import (
    REVIEW_VERSION,
    ReviewerKeyPolicy,
    SignedReviewAuthority,
    review_statement,
)
from data_sync_etl.domain.concepts import empty_dictionary
from data_sync_etl.domain.indexing import digest
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from tests.unit.test_dynamic_concept_dictionary import activate
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline

KEY = b'TEST_ONLY-no-real-issuer-material-0000000'


def authority(clock):
    policy = ReviewerKeyPolicy(frozenset({'reviewer-test'}), frozenset({'computing'}),
        frozenset({'approved', 'rejected'}), frozenset({'term', 'relation'}), 1000)
    return SignedReviewAuthority({'issuer-v1': KEY, 'issuer-v2': KEY},
        policies={'issuer-v1': policy, 'issuer-v2': policy}, clock=lambda: clock[0])


def proof(candidate, kind, target, *, nonce, decision='approved', key_id='issuer-v1', **changes):
    body = dict(issuer=key_id, subject='reviewer-test', role='concept_reviewer',
        statement=review_statement(candidate, kind, target, decision), expires=150, issued_at=99,
        scope={'domain': candidate['domain'], 'kind': kind, 'action': decision}, nonce=nonce, version=REVIEW_VERSION)
    body.update(changes)
    return {**body, 'signature': hmac.new(KEY, json.dumps(body, sort_keys=True,
        separators=(',', ':'), ensure_ascii=True).encode(), sha256).hexdigest()}


def test_reviewer_scope_key_rotation_and_nonce_replay(pipeline):
    inventory, store, _, candidate = pipeline
    issuer = authority([100])
    worker = ConceptDiscoveryWorker(inventory, store, review_authority=issuer)
    args = dict(kind='relation', target=candidate['id'], decision='approved', reviewer='reviewer-test')
    with pytest.raises(ConceptBlocked, match='IDENTITY'):
        worker.ledger.decide(candidate, **args, proof=proof(candidate, 'relation', candidate['id'],
            nonce='test-nonce-000001', scope={'domain': 'biology', 'kind': 'relation', 'action': 'approved'}))
    worker.ledger.decide(candidate, **args, proof=proof(candidate, 'relation', candidate['id'], nonce='test-nonce-000002'))
    issuer.revoke_key('issuer-v1')
    with pytest.raises(ConceptBlocked, match='IDENTITY'):
        worker.ledger.decide(candidate, **args, proof=proof(candidate, 'relation', candidate['id'], nonce='test-nonce-000003'))
    worker.ledger.decide(candidate, **args,
        proof=proof(candidate, 'relation', candidate['id'], nonce='test-nonce-000004', key_id='issuer-v2'))
    with pytest.raises(ConceptBlocked, match='REPLAY'):
        worker.ledger.decide(candidate, **{**args, 'decision': 'rejected'}, proof=proof(candidate, 'relation',
            candidate['id'], nonce='test-nonce-000004', key_id='issuer-v2', decision='rejected'))


@pytest.mark.parametrize('revocation', ['expiry', 'key'])
def test_receipt_expiry_does_not_revive_after_ledger_refresh(pipeline, revocation):
    inventory, store, _, candidate = pipeline
    clock = [100]
    issuer = authority(clock)
    worker = ConceptDiscoveryWorker(inventory, store, review_authority=issuer)
    requests = [('relation', candidate['id']), *[('term', term_target(t, candidate)) for t in candidate['terms']]]
    for number, (kind, target) in enumerate(requests):
        worker.ledger.decide(candidate, kind=kind, target=target, decision='approved', reviewer='reviewer-test',
            proof=proof(candidate, kind, target, nonce=f'test-nonce-{number:06}'))
    provider = activate(inventory, store, worker)
    try:
        with provider.pin('cache') as dictionary:
            assert dictionary is not empty_dictionary()
        if revocation == 'expiry':
            clock[0] = 151
        else:
            issuer.revoke_key('issuer-v1')
        with provider.pin('cache') as dictionary:
            assert dictionary is empty_dictionary()
        worker.ledger.snapshot()
        with provider.pin('cache') as dictionary:
            assert dictionary is empty_dictionary()
        assert worker.build()['count'] == 0
    finally:
        provider.close()


def test_signed_proof_is_verified_when_audit_hash_chain_is_recomputed(pipeline):
    inventory, store, _, candidate = pipeline
    worker = ConceptDiscoveryWorker(inventory, store, review_authority=authority([100]))
    worker.ledger.decide(candidate, kind='relation', target=candidate['id'], decision='approved',
        reviewer='reviewer-test', proof=proof(candidate, 'relation', candidate['id'], nonce='test-nonce-000999'))
    target = store.path('review/decisions.jsonl')
    record = json.loads(target.read_text(encoding='utf-8'))
    record.pop('hash')
    record['proof']['signature'] = '0' * 64
    record['hash'] = digest(record)
    target.write_text(json.dumps(record) + '\n', encoding='utf-8')
    _, decisions = worker.ledger.snapshot()
    assert decisions[('relation', candidate['id'], candidate['binding'])] == 'rejected'


def test_expired_alias_cannot_revive_during_slow_ledger_reverification(pipeline, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    inventory, store, _, candidate = pipeline
    clock = [100]
    issuer = authority(clock)
    worker = ConceptDiscoveryWorker(inventory, store, review_authority=issuer)
    for index, (kind, target) in enumerate([('relation', candidate['id']),
            *[('term', term_target(t, candidate)) for t in candidate['terms']]]):
        worker.ledger.decide(candidate, kind=kind, target=target, decision='approved', reviewer='reviewer-test',
            proof=proof(candidate, kind, target, nonce=f'test-nonce-{index:06}'))
    provider = activate(inventory, store, worker)
    entered, release = Event(), Event()
    original = issuer.verify
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(issuer, 'verify', slow)
    clock[0] = 151
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(worker.ledger.snapshot)
            try:
                assert entered.wait(5)
                with provider.pin('cache') as dictionary:
                    assert dictionary is empty_dictionary()
            finally:
                release.set()
            future.result(timeout=5)
    finally:
        provider.close()


def test_oversized_review_record_is_refused_before_partial_audit_append(pipeline):
    _, store, worker, candidate = pipeline
    with pytest.raises(ConceptBlocked, match='LEDGER_BUDGET'):
        worker.ledger.decide(candidate, kind='relation', target=candidate['id'], decision='approved',
            reviewer='fixture-reviewer', proof={'TEST_ONLY_oversized': 'x' * 5000})
    assert not store.path('review/decisions.jsonl').exists()
