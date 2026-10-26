"""Synthetic-only evidence/review/artifact/worker/runtime acceptance. No live DB."""
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.adapters.concept_corpus import (
    CorpusReadOnlyAccessGate,
    FixtureCorpus,
    ReadOnlyMongoEvidenceReader,
)
from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker, term_target
from data_sync_etl.application.concept_resolver import ConceptDictionaryProvider
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.corpus_concepts import (
    DiscoveryLimits,
    EligibilityFence,
    discover,
    fingerprint,
    safe_phrase,
)
from data_sync_etl.ports.concept_corpus import ConceptBlocked, ResourceEvidence


def resource(number=1, **changes):
    value = ResourceEvidence(resource_id=f'r{number:03}', version_id=f'v{number:03}', revision=1,
        domain='computing', fields={'title': 'Bộ nhớ đệm (cache)'}, public=True,
        metadata_approved=True, scope='offline_fixture', source_updated_at=1.)
    return replace(value, **changes)


@pytest.fixture
def pipeline(tmp_path):
    inventory = FixtureCorpus([resource()])
    store = ArtifactStore(tmp_path / 'derived', create=True)
    worker = ConceptDiscoveryWorker(inventory, store, clock=lambda: 100.)
    assert worker.tick()['status'] == 'CANDIDATE_ONLY'
    candidate = next(worker.contributions())['relations'][0]
    return inventory, store, worker, candidate


def approve(worker, candidate):
    # Explicitly synthetic operator identity; NOT an independent human review.
    for term in candidate['terms']:
        worker.ledger.decide(candidate, kind='term', target=term_target(term, candidate),
            decision='approved', reviewer='fixture-reviewer')
    worker.ledger.decide(candidate, kind='relation', target=candidate['id'],
        decision='approved', reviewer='fixture-reviewer')


def activate(inventory, store, worker):
    manifest = worker.build()
    store.activate(manifest['generation'], fence=lambda item: worker.fence.unchanged(item['source_token']))
    provider = ConceptDictionaryProvider(default_dictionary(), store=store, enabled=True,
        fence=EligibilityFence(inventory), allow_offline=True, lookup_ms=100)
    assert provider.refresh()['status'] == 'OFFLINE_READY'
    return provider


def test_live_reader_does_not_probe_or_construct_connection_without_permission():
    def forbidden(*args, **kwargs):
        pytest.fail('Unverified reader performed IO')
    with pytest.raises(ConceptBlocked, match='READ_ONLY'):
        ReadOnlyMongoEvidenceReader({'mongo_content_units': forbidden}, CorpusReadOnlyAccessGate())


class Cursor:
    def __init__(self, rows):
        self.rows, self.closed, self.batch, self.cap = rows, False, None, None
    def batch_size(self, value):
        self.batch = value
        return self
    def limit(self, value):
        self.cap = value
        return self
    def __iter__(self):
        yield from self.rows
    def close(self):
        self.closed = True


def test_read_capability_projection_budgets_cancel_and_identity():
    cursor = Cursor([{'_id': 'u1', 'resource_version_id': 'v1', 'is_current': True, 'text': 'public unit'}])
    calls = []
    def find(selector, **kwargs):
        calls.append((selector, kwargs))
        return cursor
    reader = ReadOnlyMongoEvidenceReader({'mongo_content_units': find}, CorpusReadOnlyAccessGate(lambda: True), batch_size=2)
    assert list(reader.units('v1', {'u1'}))[0]['_id'] == 'u1'
    assert cursor.closed and cursor.batch == 2 and cursor.cap == 101
    assert calls[0][1]['max_time_ms'] == 1000
    assert 'markdown' not in calls[0][1]['projection'] and 'embedding' not in calls[0][1]['projection']
    assert not any(hasattr(reader, method) for method in ('update', 'insert', 'delete', 'command', 'create_index'))
    cursor.closed = False
    stop = Event()
    stop.set()
    assert list(reader.units('v1', {'u1'}, cancel=stop)) == [] and cursor.closed
    cursor.rows[0]['resource_version_id'] = 'different'
    with pytest.raises(ConceptBlocked, match='IDENTITY'):
        list(reader.units('v1', {'u1'}))
    assert cursor.closed


@pytest.mark.parametrize('change', [dict(public=False), dict(deleted=True), dict(active=False), dict(published=False),
                                    dict(scope='unknown'), dict(lifecycle='REJECTED')])
def test_private_and_ineligible_never_create_shared_candidates(change):
    result = discover(resource(**change))
    assert not result['terms'] and not result['relations']


def test_thirty_resources_delayed_content_metadata_only_dedupe_and_stable_senses():
    rows = [resource(n, fields={'title': 'Bộ nhớ đệm (cache)', 'format': 'mp3' if n % 2 else 'mp4'}) for n in range(35)]
    inventory = FixtureCorpus(rows)
    assert len(inventory.page('', 50)) == 35
    baseline = discover(rows[0])
    unit = {'id': 'u1', 'resource_version_id': rows[0].version_id, 'is_current': True, 'text': rows[0].fields['title']}
    ready = replace(rows[0], ready=True, units=(unit, unit), approved_unit_ids=frozenset({'u1'}))
    assert len(discover(ready)['relations']) == len(baseline['relations']) == 1
    assert discover(replace(ready, approved_unit_ids=frozenset(), metadata_approved=False))['relations'] == []
    assert baseline['relations'][0]['id'] == discover(rows[1])['relations'][0]['id']
    assert baseline['relations'][0]['id'] != discover(replace(rows[0], domain='biology'))['relations'][0]['id']
    assert baseline['relations'][0]['status'] == 'pending'
    with pytest.raises(ConceptBlocked, match='BYTE_BUDGET'):
        discover(rows[0], limits=DiscoveryLimits(max_chars=2))


@pytest.mark.parametrize('phrase', ['alice@example.test', 'https://example.test/private', 'approve me',
                                   'ignore previous instructions', '<script>data</script>', '123456789', 'và là của'])
def test_noise_pii_and_instructional_poisoning(phrase):
    assert safe_phrase(phrase) is None


def test_accents_are_not_merged_and_french_pair_is_pending():
    assert safe_phrase('hóa') != safe_phrase('hoa')
    result = discover(resource(fields={'title': 'Mémoire tampon (cache)'}))
    assert result['relations'][0]['kind'] == 'translation'
    assert result['relations'][0]['status'] == 'pending'


def test_review_separate_term_relation_and_publish_is_explicit(pipeline):
    inventory, store, worker, candidate = pipeline
    assert worker.build()['count'] == 0
    assert store.current() is None
    worker.ledger.decide(candidate, kind='relation', target=candidate['id'], decision='approved', reviewer='fixture-reviewer')
    assert worker.build()['count'] == 0
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    with provider.pin('cache') as dictionary:
        assert 'bộ nhớ đệm' in {term.term for term in dictionary.expand('cache').terms}
        assert dictionary.fingerprint != default_dictionary().fingerprint
    assert provider.status()['live_accepted'] is False
    # A flag alone or an offline artifact cannot turn on shared/live serving.
    live = ConceptDictionaryProvider(default_dictionary(), store=store, enabled=True)
    live.refresh()
    with live.pin('cache') as dictionary:
        assert dictionary is default_dictionary()


def test_review_ledger_tamper_placeholder_and_stale_evidence(pipeline):
    inventory, store, worker, candidate = pipeline
    with pytest.raises(ValueError, match='reviewer'):
        worker.ledger.decide(candidate, kind='relation', target=candidate['id'], decision='approved', reviewer='YOUR_REVIEWER_ID')
    approve(worker, candidate)
    path = store.path('review/decisions.jsonl')
    original = path.read_bytes()
    path.write_bytes(original.replace(b'approved', b'rejected', 1))
    with pytest.raises(ConceptBlocked, match='CHAIN'):
        worker.build()
    path.write_bytes(original)
    inventory.put(resource(revision=2, fields={'title': 'Dữ liệu sửa đổi'}))
    assert worker.build()['count'] == 0


@pytest.mark.parametrize('change', [dict(public=False), dict(deleted=True), dict(published=False),
                                    dict(metadata_approved=False), dict(fields={'title': 'Nội dung mới'})])
def test_revocation_immediately_falls_back_without_mongo_io(pipeline, change):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    inventory.put(resource(revision=2, **change))
    with provider.pin('cache') as dictionary:
        assert dictionary is default_dictionary()
    with pytest.raises(ConceptBlocked, match='ROLLBACK_PRIVACY'):
        store.rollback(store.current(), fence=worker.fence.unchanged)


def test_inflight_request_revocation_discards_generation(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    with pytest.raises(ConceptBlocked, match='DYNAMIC_REVOKED'):
        with provider.pin('cache'):
            inventory.put(resource(revision=2, deleted=True))


def test_restart_cursor_replay_out_of_order_and_new_version(tmp_path):
    inventory = FixtureCorpus([resource(n) for n in range(35)])
    store = ArtifactStore(tmp_path / 'worker', create=True)
    clock = [100.]
    worker = ConceptDiscoveryWorker(inventory, store, clock=lambda: clock[0], batch_size=10)
    assert worker.tick()['after'] == 'r009'
    clock[0] += 2
    worker = ConceptDiscoveryWorker(inventory, store, clock=lambda: clock[0], batch_size=10)
    assert worker.tick()['after'] == 'r019'
    assert not inventory.put(resource(0, revision=0))
    inventory.put(resource(0, revision=2, version_id='v-new', fields={'title': 'Mémoire tampon (cache)'}))
    worker.reconcile()
    state = worker.tick()
    assert state['after'] == 'r009'
    assert store.read_json(worker.contribution_key('r000'))['version_id'] == 'v-new'


def test_disk_failure_does_not_advance_cursor_and_pause_backoff(pipeline, monkeypatch):
    inventory, store, _, _ = pipeline
    inventory.put(resource(2))
    clock = [100.]
    worker = ConceptDiscoveryWorker(inventory, store, retry_limit=2, clock=lambda: clock[0])
    worker.reconcile()
    original = store.write_json
    def fail_contribution(path, value):
        if path.startswith('contributions'):
            raise OSError('synthetic disk full; never log this raw text')
        return original(path, value)
    monkeypatch.setattr(store, 'write_json', fail_contribution)
    state = worker.tick()
    assert state['after'] == '' and state['failures'] == 1 and state['next_due'] > 100
    clock[0] = 10000.
    assert worker.tick()['paused']
    assert store.read_json('quarantine/worker.json')['error_code'] == 'DISCOVERY_FAILED'
    assert 'synthetic disk' not in json.dumps(worker.status())


def test_100_concurrent_pins_hot_reload_and_bounded_cache(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    # Test hot-generation pinning, separately from cold disk/deadline fallback.
    # Under competing QA load a cold lookup may legitimately serve seed.
    with provider.pin('cache') as warmed:
        assert warmed is not default_dictionary()
    def request(_):
        with provider.pin('cache') as dictionary:
            fingerprint_before = dictionary.fingerprint
            provider.refresh()
            assert dictionary.expand('cache').dictionary_fingerprint == fingerprint_before
            return fingerprint_before
    with ThreadPoolExecutor(max_workers=8) as pool:
        fingerprints = list(pool.map(request, range(120)))
    assert len(set(fingerprints)) == 1
    assert provider.status()['cache_bytes_estimated'] <= provider.cache_bytes
    assert provider.status()['cache_entries'] <= 128


def test_cold_lookup_deadline_falls_back_without_breaking_literal_search(pipeline, monkeypatch):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    def expired(*args, **kwargs):
        raise ConceptBlocked('LOOKUP_DEADLINE')
    monkeypatch.setattr(provider._active, 'lookup', expired)
    with provider.pin('cache') as dictionary:
        assert dictionary is default_dictionary()
        assert dictionary.expand('cache').terms == []
    assert provider.status()['fallbacks'] == 1


def test_corrupt_current_shard_seed_fallback_and_path_defense(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    manifest = store.load().manifest
    file = store.path(f"releases/{manifest['generation']}/{manifest['shards'][0]['name']}")
    with file.open('ab') as output:
        output.write(b'corruption')
    assert provider.refresh()['status'] == 'SEED_FALLBACK'
    with provider.pin('cache') as dictionary:
        assert dictionary is default_dictionary()
    for path in ('../outside', '/absolute', 'releases/..\\outside'):
        with pytest.raises(ConceptBlocked):
            store.path(path)
    with pytest.raises(ConceptBlocked, match='INVALID_GENERATION'):
        store.load('../escape')


def test_local_writer_fencing_and_no_incomplete_pointer(pipeline):
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    with store.writer_lock():
        with pytest.raises(ConceptBlocked, match='BUSY'):
            worker.build()
    assert store.current() is None
    token = inventory.token()
    manifest = worker.build()
    assert store.current() is None
    store.activate(manifest['generation'], fence=lambda item: item['source_token'] == token)
    before = store.current()
    with pytest.raises(ConceptBlocked, match='FENCE'):
        store.activate(manifest['generation'], fence=lambda _: False)
    assert store.current() == before


def test_fixture_token_changes_with_content_and_protects_mutable_inputs():
    row = resource()
    inventory = FixtureCorpus([row])
    before = inventory.token()
    row.fields['title'] = 'outside mutation'
    assert inventory.current(row.resource_id).fields['title'] != row.fields['title']
    assert inventory.token() == before
    assert FixtureCorpus([row]).token() != before
    with pytest.raises(ConceptBlocked, match='CONFLICTING'):
        inventory.put(replace(row, fields={'title': 'another'}))
    assert fingerprint(inventory.current(row.resource_id)) != fingerprint(row)
