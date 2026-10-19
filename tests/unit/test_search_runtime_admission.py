from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import Event
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable

ACCESS = SearchAccess('local_demo')


def test_search_runtime_worker_capacity_is_bounded_and_released():
    service = SearchService(None, SimpleNamespace(search_timeout_seconds=15), None)
    with ExitStack() as stack:
        for _ in range(16):
            stack.enter_context(service.runtime_admission('vector', access=ACCESS))
        with pytest.raises(SearchError, match='SEARCH_RUNTIME_BUSY'):
            with service.runtime_admission('hybrid', access=ACCESS):
                pytest.fail('worker capacity must remain bounded')
        with service.runtime_admission('bow', access=ACCESS):
            pass
        with service.runtime_admission('vector', access=SearchAccess()):
            pass  # public fail-closed response does not use the model
    with pytest.raises(RuntimeError), service.runtime_admission('hybrid', access=ACCESS):
        raise RuntimeError('controlled failure')
    with service.runtime_admission('vector', access=ACCESS):
        pass


def test_http_search_queues_competing_model_work_but_keeps_bow_and_recovers(searchable):
    c, _ = searchable
    entered, release = Event(), Event()
    original = c.indexing.embedding.prepare

    def blocked():
        entered.set()
        assert release.wait(5)
        return original()

    c.indexing.embedding.prepare = blocked
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})
            try:
                assert entered.wait(3)
                waiting = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'hybrid'})
                queue = c.search._http_inference
                with queue._condition:
                    assert queue._condition.wait_for(lambda: len(queue._queue) == 1, timeout=3)
                assert not waiting.done()
                assert api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
                assert api.get('/health').status_code == 200
            finally:
                release.set()
            assert first.result(timeout=5).status_code == 200
            assert waiting.result(timeout=5).status_code == 200
        c.indexing.embedding.prepare = original
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'}).status_code == 200
