"""Additional acceptance cases; deterministic384D doubles are not a real model."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unicodedata import normalize

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.application.http_query_cache import HttpQueryVectorCache
from data_sync_etl.domain.core import now
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.search_query_cache_support import Runtime384, configure
from tests.unit.test_http_query_cache import encode
from tests.unit.test_search import searchable as searchable

ACCESS = SearchAccess('local_demo')


def outcome(service, cached=True, **kwargs):
    try:
        return (service.search_http if cached else service.search)(access=ACCESS, **kwargs)
    except (SearchError, IndexingError) as exc:
        return {'safe_error': exc.code, 'status': exc.status}


def parity(service, **kwargs):
    assert outcome(service, True, **kwargs) == outcome(service, False, **kwargs)


def test_exact_normalization_and_large_bounded_soak():
    model = Runtime384()
    clock = [0.]
    cache = HttpQueryVectorCache(model, clock=lambda: clock[0])
    words = ['học liệu', 'Học liệu', 'hoc lieu', 'học  liệu']
    for text in words:
        encode(cache, model, text)
    assert model.encodes == 4
    encode(cache, model, '  ' + normalize('NFD', words[0]) + '  ')
    assert model.encodes == 4
    for index in range(1000):
        encode(cache, model, 'novel ' + str(index))
        stats = cache.stats()
        assert stats['entries'] <= 64 and stats['accounted_bytes'] <= 1048576
    assert cache.stats()['entries'] == 64 and cache.stats()['evicted'] >= 940
    clock[0] = 60
    assert cache.stats()['entries'] == 0


@pytest.mark.parametrize('change', ['bag_keywords', 'bag_delete', 'mongo_keywords', 'mongo_delete',
                                    'pg_unit_delete', 'source_missing', 'stage_pending', 'postprocess_failed'])
def test_live_mutation_parity_without_timestamp_markers(searchable, change):
    c, version = searchable
    configure(c, version)
    assert c.search.search_http('page text', 'hybrid', access=ACCESS)['items']
    root = next(iter(c.indexing.bags.rows))
    leaf = next(iter(c.documents.units))
    if change == 'bag_keywords':
        c.indexing.bags.rows[root].keywords = ['differentword']
    elif change == 'bag_delete':
        del c.indexing.bags.rows[root]
    elif change == 'mongo_keywords':
        c.documents.units[leaf]['keywords'] = ['changedword']
    elif change == 'mongo_delete':
        del c.documents.units[leaf]
    else:
        with c.uow() as repo:
            if change == 'pg_unit_delete':
                unit = repo.find('content_units', {'resource_version_id': version['id']}, order=('-sequence_no',))[0]
                repo.delete('content_unit_texts', {'content_unit_id': unit['id']})
                repo.delete('content_units', {'id': unit['id']})
            elif change == 'source_missing':
                repo.update('master_learning_resources', version['resource_id'], {'source_missing_at': now()})
            else:
                stage = 'POSTPROCESS' if change == 'postprocess_failed' else 'INDEX_BUILD'
                run = repo.find('etl_stage_runs', {'resource_version_id': version['id'], 'stage': stage},
                                order=('-created_at', '-id'), limit=1)[0]
                repo.update('etl_stage_runs', run['id'], {'status': 'FAILED' if stage == 'POSTPROCESS' else 'PENDING'})
    for method in ('bow', 'vector', 'hybrid'):
        parity(c.search, query='page text', method=method, page=1, size=5)


def test_all_filters_and_cache_on_edge_responses(searchable):
    c, version = searchable
    configure(c, version)
    with c.uow() as repo:
        subject = repo.find('resource_subjects', {'resource_id': version['resource_id']})[0]['subject_id']
        grade = repo.find('resource_grade_levels', {'resource_id': version['resource_id']})[0]['grade_level_id']
        kind = repo.get('master_learning_resources', version['resource_id'])['resource_type_code']
    filters = dict(subject_id=subject, grade_level_id=grade, resource_type_code=kind,
                   resource_id=version['resource_id'], resource_version_id=version['id'])
    for method in ('bow', 'vector', 'hybrid'):
        for key, value in filters.items():
            for selected in (value, 'missing', None):
                parity(c.search, query='page text', method=method, filters={key: selected})
        for query in ('page', '999999', 'unmatchedword', 'và của'):
            for page, size in ((1, 5), (2, 10), (1, 20), (1001, 1)):
                parity(c.search, query=query, method=method, page=page, size=size)
    c.settings.search_max_bags = 1
    parity(c.search, query='page text', method='hybrid')


@pytest.mark.parametrize('searchable', [{'structured': True}], indirect=True)
def test_cached_vector_does_not_hide_revoked_ancestor_or_context(searchable):
    c, version = searchable
    configure(c, version)
    items = c.search.search_http('page', 'hybrid', access=ACCESS)['items']
    assert items
    with c.uow() as repo:
        parent = next(row for row in repo.find('content_units', {'resource_version_id': version['id']})
                      if row['parent_unit_id'] is None)
        repo.update('content_units', parent['id'], {'retrieval_eligible': False})
    for method in ('bow', 'vector', 'hybrid'):
        parity(c.search, query='page', method=method)
    assert not c.search.search_http('page', 'hybrid', access=ACCESS)['items']
    with pytest.raises(SearchError):
        c.search.context(items[0]['context_id'], access=ACCESS)


@pytest.mark.parametrize('cache_enabled', [False, True])
@pytest.mark.parametrize('warm', [False, True])
@pytest.mark.parametrize('concurrency', [1, 2, 5, 10])
def test_busy_failure_rate_and_slot_release(searchable, monkeypatch, cache_enabled, warm, concurrency):
    c, version = searchable
    model = configure(c, version)
    c.settings.search_query_embedding_cache_enabled = cache_enabled
    entered, release = Event(), Event()
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        if warm:
            assert api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'}).status_code == 200
        original = c.search.bags.search_candidates
        def held(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(15)
            return original(*args, **kwargs)
        monkeypatch.setattr(c.search.bags, 'search_candidates', held)
        with ThreadPoolExecutor(max_workers=concurrency + 1) as pool:
            first = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})
            try:
                assert entered.wait(10)
                rivals = list(pool.map(lambda index: api.post('/api/v1/search', json={
                    'query': 'page' if index % 2 else 'different', 'method': 'hybrid'}), range(concurrency - 1)))
                assert len(rivals) == concurrency - 1
                assert all(row.status_code == 503 and row.json()['detail']['code'] == 'SEARCH_RUNTIME_BUSY' for row in rivals)
                assert api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
            finally:
                release.set()
            assert first.result(timeout=15).status_code == 200
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'}).status_code == 200
        assert model.loads == 1


@pytest.mark.parametrize('failure', ['inference', 'deadline', 'read_error'])
def test_failed_request_no_publication_and_guard_recovers(searchable, monkeypatch, failure):
    c, version = searchable
    model = configure(c, version)
    cache = c.search._http_query_vectors
    with monkeypatch.context() as patch:
        if failure == 'inference':
            patch.setattr(model, '_encode', lambda _: (_ for _ in ()).throw(IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503)))
        elif failure == 'deadline':
            patch.setattr(c.search, '_deadline', lambda _: (_ for _ in ()).throw(SearchError('SEARCH_TIMEOUT', 503)))
        else:
            patch.setattr(c.search.bags, 'search_candidates', lambda *_a, **_k: (_ for _ in ()).throw(SearchError('SEARCH_SCHEMA_UNAVAILABLE')))
        with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
            assert api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'}).status_code == 503
        assert cache.stats()['entries'] == 0
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'}).status_code == 200


def test_event_barrier_writer_partial_state_after_ready_snapshot(searchable, monkeypatch):
    c, version = searchable
    configure(c, version)
    c.search.search_http('page', 'hybrid', access=ACCESS)
    original = c.search._ready_snapshot
    entered, changed = Event(), Event()
    def observed(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        assert snapshot is not None
        entered.set()
        assert changed.wait(10)
        return snapshot
    monkeypatch.setattr(c.search, '_ready_snapshot', observed)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(outcome, c.search, True, query='page', method='hybrid')
        assert entered.wait(10)
        for bag in c.indexing.bags.rows.values():
            bag.is_current = False  # Simulate writer before PG INDEX completion; no PG marker update.
        changed.set()
        result = pending.result(timeout=10)
    monkeypatch.setattr(c.search, '_ready_snapshot', original)
    control = outcome(c.search, False, query='page', method='hybrid')
    assert result.get('safe_error') == 'SEARCH_INDEX_STALE'
    assert control['items'] == []  # Different instant, but neither exposes stale/private contents.
    assert c.search._http_query_vectors.stats()['entries'] == 0

def test_live_catalog_and_provenance_filters(searchable):
    c, version = searchable
    configure(c, version)
    with c.uow() as repo:
        subject = repo.find('resource_subjects', {'resource_id': version['resource_id']})[0]['subject_id']
        grade = repo.find('resource_grade_levels', {'resource_id': version['resource_id']})[0]['grade_level_id']
    cases = [('subjects', subject, 'is_active', False, {'subject_id': subject}),
             ('subjects', subject, 'deleted_at', now(), {'subject_id': subject}),
             ('master_grade_levels', grade, 'grade_is_active', False, {'grade_level_id': grade}),
             ('master_grade_levels', grade, 'grade_deleted_at', now(), {'grade_level_id': grade})]
    for table, identifier, field, changed, filters in cases:
        with c.uow() as repo:
            previous = repo.get(table, identifier)[field]
            repo.update(table, identifier, {field: changed})
        for method in ('bow', 'vector', 'hybrid'):
            parity(c.search, query='page', method=method, filters=filters)
            assert not c.search.search_http('page', method, filters=filters, access=ACCESS)['items']
        with c.uow() as repo:
            repo.update(table, identifier, {field: previous})
    for origin in ('SOURCE', 'LOCAL'):
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'source_origin': origin})
        for method in ('bow', 'vector', 'hybrid'):
            parity(c.search, query='page', method=method)


def test_invalid_requests_and_untrusted_scope_do_not_consult_cache(searchable):
    c, version = searchable
    model = configure(c, version)
    c.search.search_http('page', 'vector', access=ACCESS)
    encoded = model.encodes
    before = c.search._http_query_vectors.stats()
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        for query in ('x' * 301, '', '   ', None, ['page']):
            assert api.post('/api/v1/search', json={'query': query, 'method': 'vector'}).status_code == 422
        for field in ('scope', 'role', 'user_id'):
            assert api.post('/api/v1/search', json={'query': 'page', field: 'local_demo'}).status_code == 422
    assert model.encodes == encoded and c.search._http_query_vectors.stats()['hit'] == before['hit']
    result = c.search.search_http('page', 'vector', access=SearchAccess())
    assert result['items'] == [] and result['access_scope'] == 'public'
    assert model.encodes == encoded and c.search._http_query_vectors.stats()['entries'] == 0


def test_live_relation_deletion_and_recreation(searchable):
    c, version = searchable
    configure(c, version)
    for table, field, filter_key in (
        ('resource_subjects', 'subject_id', 'subject_id'),
        ('resource_grade_levels', 'grade_level_id', 'grade_level_id'),
    ):
        with c.uow() as repo:
            row = repo.find(table, {'resource_id': version['resource_id']})[0]
        filters = {filter_key: row[field]}
        assert c.search.search_http('page', 'vector', filters=filters, access=ACCESS)['items']
        with c.uow() as repo:
            repo.delete(table, {'resource_id': version['resource_id'], field: row[field]})
        for method in ('bow', 'vector', 'hybrid'):
            parity(c.search, query='page', method=method, filters=filters)
            assert c.search.search_http('page', method, filters=filters, access=ACCESS)['items'] == []
        with c.uow() as repo:
            repo.insert(table, row)
        assert c.search.search_http('page', 'vector', filters=filters, access=ACCESS)['items']


def test_retained_python_cache_heap_is_bounded_and_released():
    import gc
    import tracemalloc

    model = Runtime384()
    model.prepare()
    gc.collect()
    tracemalloc.start()
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        cache = HttpQueryVectorCache(model)
        for index in range(1000):
            encode(cache, model, 'bounded ' + str(index))
        gc.collect()
        first = tracemalloc.get_traced_memory()[0]
        assert cache.stats()['entries'] == 64
        assert first - baseline < 1048576
        for index in range(1000, 3000):
            encode(cache, model, 'bounded ' + str(index))
        gc.collect()
        assert tracemalloc.get_traced_memory()[0] - first < 65536
        assert cache.stats()['accounted_bytes'] <= 1048576
        del cache
        gc.collect()
        assert tracemalloc.get_traced_memory()[0] - baseline < 65536
    finally:
        tracemalloc.stop()
