"""Managed startup states and HTTP guards, no downloaded model or Dev data."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.main import create_app, shutdown_runtime
from tests.unit.test_search import searchable as searchable


def runtime():
    return LocalSentenceEmbedding('', 'minilm', 'revision', 384)


def test_startup_warms_analyzer_before_serving_once(searchable, monkeypatch):
    container, _ = searchable
    calls = []
    monkeypatch.setattr('data_sync_etl.domain.lexical.analyze', lambda text: calls.append(text) or [])
    with TestClient(create_app(container), base_url='http://127.0.0.1') as client:
        assert len(calls) == 1
        assert client.get('/health').status_code == 200
        assert len(calls) == 1


def test_one_background_loader_per_process_and_readonly_status(monkeypatch):
    model = runtime()
    entered, release = Event(), Event()
    calls = []
    def prepare():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        model._checked = True
    monkeypatch.setattr(model, 'prepare', prepare)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _: model.start_preload(), range(5)))
    try:
        assert entered.wait(2)
        for _ in range(10):
            assert model.search_status() == {'state': 'LOADING', 'ready': False}
        with pytest.raises(SearchError, match='SEARCH_MODEL_LOADING'):
            model.require_search_ready()
        assert calls == [1]
        release.set()
        model._preload_thread.join(3)
        assert model.search_status() == {'state': 'READY', 'ready': True}
        model.require_search_ready()
        model.start_preload()
        assert calls == [1]
    finally:
        release.set()
        model.stop_preload()
    assert model.search_status() == {'state': 'STOPPED', 'ready': False}


@pytest.mark.parametrize('recover', [False, True])
def test_bounded_retry_and_sanitized_failure(monkeypatch, recover):
    model, calls = runtime(), []
    def prepare():
        calls.append(1)
        if not recover or len(calls) == 1:
            raise RuntimeError('PRIVATE_MODEL_PATH')
        model._checked = True
    monkeypatch.setattr(model, 'prepare', prepare)
    model.start_preload()
    model._preload_thread.join(4)
    assert len(calls) == 2
    assert model.search_status()['state'] == ('READY' if recover else 'FAILED')
    assert 'PRIVATE' not in str(model.search_status())
    if not recover:
        with pytest.raises(SearchError, match='BLOCKED_EMBEDDING_RUNTIME'):
            model.require_search_ready()
        recover = True
        assert model.retry_preload()
        model._preload_thread.join(2)
        assert len(calls) == 3
        assert model.search_status()['ready']
    assert not model.retry_preload()
    model.stop_preload()


def test_shutdown_during_load_does_not_publish_ready(monkeypatch):
    model, entered, release = runtime(), Event(), Event()
    def prepare():
        entered.set()
        assert release.wait(5)
        model._checked = True
    monkeypatch.setattr(model, 'prepare', prepare)
    model.start_preload()
    assert entered.wait(2)
    model.stop_preload()
    release.set()
    model._preload_thread.join(2)
    assert model.search_status() == {'state': 'STOPPED', 'ready': False}
    assert runtime().search_status() == {'state': 'UNINITIALIZED', 'ready': False}


def test_shutdown_failure_does_not_emit_false_failed_or_restart(monkeypatch, caplog):
    model, entered, release = runtime(), Event(), Event()
    def prepare():
        entered.set()
        assert release.wait(3)
        raise RuntimeError('interpreter shutdown')
    monkeypatch.setattr(model, 'prepare', prepare)
    model.start_preload()
    assert entered.wait(2)
    model.request_stop()
    release.set()
    model._preload_thread.join(2)
    assert model.search_status()['state'] == 'STOPPED'
    assert 'search_model FAILED' not in caplog.text
    original = model._preload_thread
    model.start_preload()
    assert model._preload_thread is original


def test_cancelled_shutdown_still_signals_stop_and_closes_owned_resources(monkeypatch):
    async def exercise():
        model, closed, entered = runtime(), [], asyncio.Event()
        async def blocked_stop(_):
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr('starlette.concurrency.run_in_threadpool', blocked_stop)
        task = asyncio.create_task(shutdown_runtime(model, lambda: closed.append(True)))
        await asyncio.wait_for(entered.wait(), 2)
        assert model.search_status()['state'] == 'STOPPED'
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert closed == [True]
    asyncio.run(exercise())


def test_recovery_exhaustion_and_concurrent_retry_are_bounded(monkeypatch):
    model, calls = runtime(), []
    def failed():
        calls.append(1)
        raise RuntimeError('PRIVATE_LOAD_FAILURE')
    monkeypatch.setattr(model, 'prepare', failed)
    monkeypatch.setattr(model._stop, 'wait', lambda _: False)
    model.start_preload()
    model._preload_thread.join(2)
    assert len(calls) == 2
    with ThreadPoolExecutor(max_workers=5) as pool:
        assert sum(pool.map(lambda _: model.retry_preload(), range(5))) == 1
    model._preload_thread.join(2)
    assert len(calls) == 4
    assert model.search_status() == {'state': 'FAILED', 'ready': False}
    assert not model.retry_preload()
    model.stop_preload()


def test_inference_failure_cannot_leave_false_ready(monkeypatch):
    model = runtime()
    monkeypatch.setattr(model, 'prepare', lambda: setattr(model, '_checked', True))
    model.start_preload()
    model._preload_thread.join(2)
    class Broken:
        def encode(self, *args, **kwargs):
            raise ValueError('PRIVATE_INFERENCE')
    model._model = Broken()
    with pytest.raises(IndexingError):
        model._encode('xin chào')
    assert model.search_status() == {'state': 'FAILED', 'ready': False}
    model.stop_preload()


def test_lifespan_preloads_health_and_status_do_not_load_and_bow_independent(searchable, monkeypatch):
    c, _ = searchable
    original, managed = c.indexing.embedding, runtime()
    entered, release = Event(), Event()
    calls = []
    def prepare():
        calls.append(1)
        entered.set()
        assert release.wait(5)
        managed._checked = True
    monkeypatch.setattr(managed, 'prepare', prepare)
    monkeypatch.setattr(c.indexing, 'embedding', managed)
    try:
        with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
            assert entered.wait(2)
            assert api.get('/health').status_code == 200
            response = api.get('/api/v1/search/model-status')
            assert response.json() == {'state': 'LOADING', 'ready': False}
            assert response.headers['cache-control'] == 'private, no-store'
            for method in ('vector', 'hybrid'):
                response = api.post('/api/v1/search', json={'query': 'page', 'method': method})
                assert response.status_code == 503
                assert response.json()['detail']['code'] == 'SEARCH_MODEL_LOADING'
                assert response.headers['retry-after'] == '2'
            # Existing snapshot/identity contract remains that of the fixture.
            managed.model_id, managed.version, managed.dimension = original.model_id, original.version, original.dimension
            managed.available = original.available
            assert api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
            assert calls == [1]
            release.set()
            managed._preload_thread.join(3)
            assert api.get('/api/v1/search/model-status').json()['ready'] is True
            assert api.post('/api/v1/search/model-retry').status_code == 503  # READY cannot trigger reload.
            c.settings.search_internal_dev_enabled = False
            assert api.post('/api/v1/search/model-retry').status_code == 403
    finally:
        release.set()
        managed.stop_preload()
