"""P02: poison event isolation and immediate fixture source-outage fencing."""
from data_sync_etl.application.concept_scheduler import ConceptWorkerScheduler
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.indexing import digest
from tests.unit.test_dynamic_concept_dictionary import activate, approve
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline


def test_poison_event_does_not_starve_valid_revoke_or_leak_payload(pipeline):
    inventory, store, worker, _ = pipeline
    from dataclasses import replace
    revoked = replace(inventory.current('r001'), revision=2, public=False)
    inventory.put(revoked)
    worker.events.enqueue(revoked, 'revoke')
    pending = store.read_json('events/pending.json')
    pending['bad'] = {'kind': 'unknown', 'secret': 'must-not-enter-quarantine'}
    store.write_json('events/pending.json', pending)
    worker.tick()
    assert worker.status()['urgent_revocations'] == 1
    receipts = list(store.path('quarantine/events').glob('*.json'))
    assert len(receipts) == 1 and 'must-not-enter' not in receipts[0].read_text()


def test_loader_outage_invalidates_existing_provider_without_waiting_for_rebuild(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    try:
        with provider.pin('cache') as before:
            assert before is not default_dictionary()
        scheduler = ConceptWorkerScheduler(worker, lambda: (_ for _ in ()).throw(OSError('synthetic outage')))
        assert scheduler.step()['status'] == 'SOURCE_OR_PRESSURE_UNAVAILABLE'
        with provider.pin('cache') as after:
            assert after is default_dictionary()
    finally:
        provider.close()


def test_poison_kind_with_otherwise_valid_shape_is_quarantined(pipeline):
    _, store, worker, _ = pipeline
    store.write_json('events/pending.json', {digest('r-bad'): {'resource_id': 'r-bad',
        'revision': 1, 'fingerprint': '0' * 64, 'kind': []}})
    assert worker.tick()['status'] in ('CANDIDATE_ONLY', 'PAUSED_OR_WAITING')
    assert worker.status()['quarantined_events'] == 1
