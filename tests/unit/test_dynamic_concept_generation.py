"""Actual generation switch/rollback/crash checks with fixture-only authority."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from time import monotonic, sleep

import pytest

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.domain.concepts import empty_dictionary
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from tests.unit.test_dynamic_concept_dictionary import activate, approve
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline


def test_background_hot_loader_starts_stops_and_revocation_never_waits_for_poll(pipeline):
    from data_sync_etl.application.concept_resolver import ConceptDictionaryProvider
    from data_sync_etl.domain.corpus_concepts import EligibilityFence
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    activate(inventory, store, worker)
    provider = ConceptDictionaryProvider(empty_dictionary(), store=store, enabled=True,
        fence=EligibilityFence(inventory), allow_offline=True, refresh_seconds=.01, lookup_ms=100)
    provider.start()
    try:
        deadline = monotonic() + 3
        while provider.status()['generation'] is None and monotonic() < deadline:
            sleep(.01)
        assert provider.status()['generation'] == store.current()
        row = inventory.current('r001')
        inventory.put(replace(row, revision=2, public=False))
        with provider.pin('cache') as dictionary:
            assert dictionary is empty_dictionary()
    finally:
        provider.close()
    assert provider._thread is None


def test_dynamic_flags_default_off():
    from data_sync_etl.config import Settings
    settings = Settings(_env_file=None)
    assert not settings.concept_dynamic_enabled
    assert not settings.concept_dynamic_builder_enabled
    assert not settings.concept_discovery_semantic_enabled


def test_simulated_one_hour_source_outage_has_bounded_retries_and_manual_recovery(pipeline, monkeypatch):
    from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
    inventory, store, _, _ = pipeline
    clock, calls, offline = [0.], [0], [True]
    page = inventory.page
    def source(after, limit):
        calls[0] += 1
        if offline[0]:
            raise OSError('synthetic network unavailable')
        return page(after, limit)
    monkeypatch.setattr(inventory, 'page', source)
    worker = ConceptDiscoveryWorker(inventory, store, clock=lambda: clock[0])
    worker.reconcile()
    for instant in (0, 2, 6, 14, 30, 60, 300, 900, 1800, 3600):
        clock[0] = instant
        worker.tick()
    assert worker.status()['paused'] and calls[0] == 5
    offline[0] = False
    worker.reconcile()
    result = worker.tick()
    assert result['status'] == 'CANDIDATE_ONLY' and result['error_code'] is None


def test_one_hundred_simultaneous_readers_pin_old_generation_during_publish(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    first = store.current()
    # Warm once, so this measures generation pinning rather than 100 cold SQL reads.
    with provider.pin('cache') as dictionary:
        old_fingerprint = dictionary.fingerprint
    entered, release = Barrier(101, timeout=30), Event()
    def request(_):
        with provider.pin('cache') as pinned:
            entered.wait()
            assert release.wait(30)
            return pinned.fingerprint
    with ThreadPoolExecutor(max_workers=100) as pool:
        futures = [pool.submit(request, index) for index in range(100)]
        try:
            entered.wait()
            second = store.build([], seed_hash=empty_dictionary().fingerprint, source_token=inventory.token(),
                ledger_hash=worker.ledger.snapshot()[0], expected_parent=first, fence=lambda: True, activate=True)
            assert second['generation'] != first
            assert provider.refresh()['generation'] == second['generation']
        finally:
            release.set()
        assert {future.result() for future in futures} == {old_fingerprint}
    with provider.pin('cache') as dictionary:
        assert dictionary is empty_dictionary()
    assert store.path('releases/' + first).exists()  # No GC of pinned generations.
    store.rollback(first, fence=worker.fence.unchanged)
    assert provider.refresh()['generation'] == first


def test_review_rejection_revokes_cached_mapping_before_rebuild(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    worker.ledger.decide(candidate, kind='relation', target=candidate['id'],
        decision='rejected', reviewer='fixture-reviewer')
    with provider.pin('cache') as dictionary:
        assert dictionary is empty_dictionary()
    assert worker.build()['count'] == 0


def test_crash_before_pointer_flip_keeps_last_good_and_is_recoverable(pipeline, monkeypatch):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    activate(inventory, store, worker)
    first = store.current()
    original = store.write_json
    def crash(relative, value):
        if relative == 'current.json':
            raise OSError('simulated power failure')
        return original(relative, value)
    monkeypatch.setattr(store, 'write_json', crash)
    with pytest.raises(OSError):
        store.build([], seed_hash=empty_dictionary().fingerprint, source_token=inventory.token(),
            ledger_hash=worker.ledger.snapshot()[0], expected_parent=first, fence=lambda: True, activate=True)
    assert store.current() == first
    assert store.load(first).manifest['count'] == 1


def test_deterministic_streaming_shards_independent_of_insertion_order(pipeline, tmp_path):
    _, source, worker, candidate = pipeline
    approve(worker, candidate)
    manifest = worker.build()
    # This assertion measures canonical layout, not the configured query SLO.
    concept = source.load(manifest['generation']).lookup(['cache'], budget_ms=1000)[0]
    second = {**concept, 'concept_id': 'corpus:second'}
    second['terms'] = [{**term, 'concept_id': 'corpus:second'} for term in concept['terms']]
    generations = []
    for number, records in enumerate(([concept, second], [second, concept])):
        store = ArtifactStore(tmp_path / f'canonical-{number}', create=True)
        result = store.build(iter(records), seed_hash='fixture', source_token='frozen', ledger_hash='fixture', fence=lambda: True)
        generations.append(result['generation'])
    assert generations[0] == generations[1]


def test_missing_revocation_completeness_or_changed_source_blocks_build(pipeline):
    inventory, _, worker, _ = pipeline
    inventory.completeness_verified = False
    with pytest.raises(ConceptBlocked, match='PRIVACY'):
        worker.build()
    inventory.completeness_verified = True
    row = inventory.current('r001')
    inventory.put(replace(row, revision=2, deleted=True))
    assert worker.build()['count'] == 0
