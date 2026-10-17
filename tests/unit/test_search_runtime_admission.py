from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable

ACCESS = SearchAccess('local_demo')


def test_search_runtime_admission_is_bounded_nonblocking_and_released():
    service = SearchService(None, None, None)

    def rejected(_):
        with pytest.raises(SearchError, match='SEARCH_RUNTIME_BUSY'):
            with service.runtime_admission('hybrid', access=ACCESS):
                pytest.fail('must not wait behind ongoing model work')

    with service.runtime_admission('vector', access=ACCESS):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(rejected, range(16)))
        with service.runtime_admission('bow', access=ACCESS):
            pass
        with service.runtime_admission('vector', access=SearchAccess()):
            pass  # public fail-closed response does not use the model
    with pytest.raises(RuntimeError), service.runtime_admission('hybrid', access=ACCESS):
        raise RuntimeError('controlled failure')
    with service.runtime_admission('vector', access=ACCESS):
        pass


def test_http_search_rejects_competing_model_work_but_keeps_bow_and_recovers(searchable):
    c, _ = searchable
    entered, release = Event(), Event()
    original = c.indexing.embedding.prepare

    def blocked():
        entered.set()
        assert release.wait(5)
        return original()

    c.indexing.embedding.prepare = blocked
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})
            try:
                assert entered.wait(3)
                denied = api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'})
                assert denied.status_code == 503
                assert denied.json()['detail']['code'] == 'SEARCH_RUNTIME_BUSY'
                assert api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
                assert api.get('/health').status_code == 200
            finally:
                release.set()
            assert first.result(timeout=5).status_code == 200
        c.indexing.embedding.prepare = original
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'}).status_code == 200
