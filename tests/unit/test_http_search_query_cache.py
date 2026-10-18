from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.search_query_cache_support import Runtime384, configure
from tests.unit.test_search import searchable as searchable

ACCESS = SearchAccess('local_demo')


def test_http_pagination_and_cross_method_response_parity(searchable):
    c, version = searchable
    model = configure(c, version)
    before = model.encodes
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        for method in ('vector', 'hybrid'):
            for page, size in ((1, 1), (2, 1), (1, 1), (3, 1), (1, 5), (10, 5)):
                body = dict(query='page text', method=method, page=page, size=size)
                result = api.post('/api/v1/search', json=body)
                assert result.status_code == 200
                assert result.json() == c.search.search(**body, access=ACCESS)
    # Direct parity controls intentionally encode on each call; HTTP only once.
    assert model.encodes - before == 13
    assert c.search._http_query_vectors.stats()['hit'] == 11
    assert c.search._http_query_vectors.stats()['published'] == 1


@pytest.mark.parametrize('mutation', ['embedding', 'bag_current', 'mongo_text', 'pg_text', 'review',
                                      'inactive', 'deleted', 'publication', 'stage', 'new_version'])
def test_live_mutations_do_not_reuse_ranked_results(searchable, mutation):
    from data_sync_etl.domain.core import now

    c, version = searchable
    model = configure(c, version)
    assert c.search.search_http('page text', 'vector', access=ACCESS)['items']
    encoded = model.encodes
    bag = next(iter(c.indexing.bags.rows.values()))
    if mutation == 'embedding':
        bag.embedding = [0.5] * 384
    elif mutation == 'bag_current':
        bag.is_current = False
    elif mutation == 'mongo_text':
        next(iter(c.documents.units.values()))['text'] += ' changed'
    else:
        with c.uow() as repo:
            if mutation in ('review', 'pg_text'):
                unit = repo.find('content_units', {'resource_version_id': version['id']})[0]
                repo.update('content_units' if mutation == 'review' else 'content_unit_texts', unit['id'],
                            {'review_status': 'REJECTED'} if mutation == 'review' else {'text': 'changed'})
            elif mutation == 'stage':
                stage = repo.find('etl_stage_runs', {'resource_version_id': version['id'], 'stage': 'INDEX_BUILD'},
                                  order=('-created_at', '-id'), limit=1)[0]
                repo.update('etl_stage_runs', stage['id'], {'status': 'FAILED'})
            elif mutation == 'new_version':
                repo.insert('resource_versions', {**version, 'id': 'new-cache-fixture-version', 'version_number': 2,
                            'mongo_id': None, 'parent_version_id': version['id']})
            else:
                value = {'inactive': {'is_active': False}, 'deleted': {'deleted_at': now()},
                         'publication': {'publication_status': 'DRAFT'}}[mutation]
                repo.update('master_learning_resources', version['resource_id'], value)
    candidate = c.search.search_http('page text', 'vector', access=ACCESS)
    assert model.encodes == encoded  # Vector cached, not corpus response.
    control = c.search.search('page text', 'vector', access=ACCESS)
    assert candidate == control


def test_default_off_direct_callers_and_scope_are_unchanged(searchable):
    from data_sync_etl.config import Settings

    assert not Settings(_env_file=None).search_query_embedding_cache_enabled
    c, version = searchable
    model = configure(c, version)
    c.settings.search_query_embedding_cache_enabled = False
    before = model.encodes
    for _ in range(2):
        c.search.search_http('page', 'vector', access=ACCESS)
    assert model.encodes == before + 2
    c.settings.search_query_embedding_cache_enabled = True
    assert c.search.search_http('page', 'vector', access=SearchAccess())['items'] == []
    assert model.encodes == before + 2
    c.search.search('page', 'vector', access=ACCESS)
    assert c.search._http_query_vectors.stats()['entries'] == 0


def test_failure_runtime_and_unknown_replacement_never_hidden(searchable, monkeypatch):
    c, version = searchable
    model = configure(c, version)
    original = c.search.bags.search_candidates
    monkeypatch.setattr(c.search.bags, 'search_candidates', lambda *_a, **_kw: (_ for _ in ()).throw(SearchError('SEARCH_TIMEOUT')))
    with pytest.raises(SearchError):
        c.search.search_http('page', 'vector', access=ACCESS)
    assert c.search._http_query_vectors.stats()['entries'] == 0
    monkeypatch.setattr(c.search.bags, 'search_candidates', original)
    c.search.search_http('page', 'vector', access=ACCESS)
    model.enabled = False
    with pytest.raises(IndexingError):
        c.search.search_http('page', 'vector', access=ACCESS)
    model.enabled = True
    replacement = Runtime384()
    c.indexing.embedding = replacement
    c.search.search_http('page', 'vector', access=ACCESS)
    c.search.search_http('page', 'vector', access=ACCESS)
    assert replacement.encodes == 2  # Unattested owner is never silently admitted.


@pytest.mark.parametrize('concurrency', [1, 2, 5, 10])
def test_http_busy_contract_even_for_cached_vector(searchable, monkeypatch, concurrency):
    c, version = searchable
    model = configure(c, version)
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        body = {'query': 'page', 'method': 'vector'}
        assert api.post('/api/v1/search', json=body).status_code == 200
        before = model.encodes
        entered, release = Event(), Event()
        original = c.search.bags.search_candidates

        def held(*args, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(15)
            return original(*args, **kwargs)

        monkeypatch.setattr(c.search.bags, 'search_candidates', held)
        with ThreadPoolExecutor(max_workers=concurrency + 1) as pool:
            first = pool.submit(api.post, '/api/v1/search', json=body)
            try:
                assert entered.wait(10)
                competitors = list(pool.map(lambda _: api.post('/api/v1/search', json=body), range(concurrency - 1)))
                assert all(item.status_code == 503 and item.json()['detail']['code'] == 'SEARCH_RUNTIME_BUSY'
                           for item in competitors)
                assert api.post('/api/v1/search', json={**body, 'method': 'bow'}).status_code == 200
            finally:
                release.set()
            assert first.result(timeout=15).status_code == 200
        assert model.encodes == before
