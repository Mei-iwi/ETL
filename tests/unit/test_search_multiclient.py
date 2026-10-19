"""T3 deterministic scheduler/HTTP tests. No Dev DB, model or credentials."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from threading import BoundedSemaphore, Event
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.application.search_runtime import (
    CURRENT_SEARCH,
    SearchCall,
    SearchInferenceQueue,
)
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable

ACCESS = SearchAccess('local_demo')


def waiters(queue, count):
    with queue._condition:
        assert queue._condition.wait_for(lambda: len(queue._queue) == count, timeout=5)


def call(queue, clock=None):
    options = {} if clock is None else {'clock': clock}
    return SearchCall(15, Mock(), queue.wake, **options)


def test_fifo_bounded_and_exception_release():
    queue = SearchInferenceQueue(max_waiting=2)
    order = []
    def work(index):
        with queue.slot(call(queue)):
            order.append(index)
    with ThreadPoolExecutor(max_workers=3) as pool:
        with queue.slot(call(queue)):
            first = pool.submit(work, 1)
            waiters(queue, 1)
            second = pool.submit(work, 2)
            waiters(queue, 2)
            with pytest.raises(SearchError, match='SEARCH_RUNTIME_BUSY'):
                with queue.slot(call(queue)):
                    pytest.fail('full queue admitted extra inference')
        first.result(timeout=5)
        second.result(timeout=5)
    assert order == [1, 2]
    with pytest.raises(RuntimeError), queue.slot(call(queue)):
        raise RuntimeError('controlled model failure')
    with queue.slot(call(queue)):
        pass
    assert not queue._running and not queue._queue


@pytest.mark.parametrize('reason', ['cancel', 'deadline'])
def test_waiter_cancel_or_deadline_removes_ticket(reason):
    queue, clock = SearchInferenceQueue(), [0.0]
    waiting = call(queue, lambda: clock[0])
    def work():
        with queue.slot(waiting):
            pytest.fail('expired/cancelled waiter executed')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with queue.slot(call(queue)):
            pending = pool.submit(work)
            waiters(queue, 1)
            if reason == 'cancel':
                waiting.cancel()
            else:
                clock[0] = 16
                queue.wake()
            with pytest.raises(SearchError, match='SEARCH_TIMEOUT'):
                pending.result(timeout=5)
            waiters(queue, 0)
    assert not queue._running


def test_abandoned_worker_lease_and_pre_dispatch_cancellation():
    capacity = BoundedSemaphore(1)
    assert capacity.acquire(blocking=False)
    active = SearchCall(15, capacity.release, lambda: None)
    assert active.claim()
    active.abandon()
    assert not capacity.acquire(blocking=False)  # actual model still running
    active.finish()
    active.finish()  # idempotent, cannot over-release
    assert capacity.acquire(blocking=False)
    queued = SearchCall(15, capacity.release, lambda: None)
    queued.abandon()
    assert not queued.claim()
    assert capacity.acquire(blocking=False)


@pytest.mark.parametrize('first_method,second_method', [
    ('vector', 'vector'), ('hybrid', 'hybrid'), ('vector', 'hybrid'), ('vector', 'bow'),
])
def test_independent_http_clients_wait_only_for_inference(searchable, monkeypatch, first_method, second_method):
    c, _ = searchable
    references = {m: c.search.search('page', m) for m in (first_method, second_method)}
    entered, release = Event(), Event()
    original = c.indexing.embedding.prepare
    def held():
        if not entered.is_set():
            entered.set()
            assert release.wait(8)
        return original()
    monkeypatch.setattr(c.indexing.embedding, 'prepare', held)
    app = create_app(c)
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(app, base_url='http://127.0.0.1',
                    client=('127.0.0.1', 50000 + i))) for i in range(2)]
        clients[0].cookies.set('session', 'independent-A')
        clients[1].cookies.set('session', 'independent-B')
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(clients[0].post, '/api/v1/search', json={'query': 'page', 'method': first_method})
            try:
                assert entered.wait(5)
                second = pool.submit(clients[1].post, '/api/v1/search',
                    json={'query': 'page', 'method': second_method})
                if second_method != 'bow':
                    waiters(c.search._http_inference, 1)
                    assert not second.done()
                else:
                    assert second.result(timeout=5).status_code == 200
                assert clients[1].get('/health').status_code == 200
                assert clients[1].post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
            finally:
                release.set()
            for pending, method in ((first, first_method), (second, second_method)):
                response = pending.result(timeout=5)
                assert response.status_code == 200 and response.json() == references[method]
    assert not c.search._http_inference._running and not c.search._http_inference._queue
    assert CURRENT_SEARCH.get() is None


def test_http_deadline_does_not_release_running_model_early(searchable, monkeypatch):
    c, _ = searchable
    c.settings.search_timeout_seconds = 1  # short TEST budget, production remains 15s
    entered, release, finished = Event(), Event(), Event()
    original, new_call = c.indexing.embedding.prepare, c.search.new_http_call
    calls = []
    def recorded(*args, **kwargs):
        value = new_call(*args, **kwargs)
        if not calls:
            finish = value.finish
            def close():
                finish()
                finished.set()
            value.finish = close
        calls.append(value)
        return value
    def held():
        entered.set()
        assert release.wait(6)
        return original()
    monkeypatch.setattr(c.search, 'new_http_call', recorded)
    monkeypatch.setattr(c.indexing.embedding, 'prepare', held)
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})
            try:
                assert entered.wait(3)
                response = pending.result(timeout=3)
                assert response.status_code == 503 and response.json()['detail']['code'] == 'SEARCH_TIMEOUT'
                assert calls[0].cancelled.is_set() and not calls[0]._closed
                assert c.search._http_inference._running and c.search._http_heavy_calls._value == 15
                assert api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
            finally:
                release.set()
            assert finished.wait(3)
        assert c.search._http_heavy_calls._value == 16
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'}).status_code == 200


@pytest.mark.parametrize('failure', ['model', 'database'])
def test_errors_release_queue_and_request_context(searchable, monkeypatch, failure):
    c, _ = searchable
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        with monkeypatch.context() as patch:
            if failure == 'model':
                patch.setattr(c.indexing.embedding, 'embed', Mock(side_effect=IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503)))
            else:
                patch.setattr(c.search.bags, 'search_candidates', Mock(side_effect=RuntimeError('private driver details')))
            response = api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'})
            assert response.status_code == 503
            assert response.json()['detail']['code'] == ('BLOCKED_EMBEDDING_RUNTIME' if failure == 'model' else 'SEARCH_UNAVAILABLE')
            assert 'private driver details' not in response.text
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'}).status_code == 200
    assert not c.search._http_inference._running and not c.search._http_inference._queue
    assert c.search._http_heavy_calls._value == 16 and CURRENT_SEARCH.get() is None


def test_http_queue_full_uses_busy_not_auth_or_timeout(searchable, monkeypatch):
    c, _ = searchable
    entered, release = Event(), Event()
    original = c.indexing.embedding.prepare
    def held():
        if not entered.is_set():
            entered.set()
            assert release.wait(8)
        return original()
    monkeypatch.setattr(c.indexing.embedding, 'prepare', held)
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        with ThreadPoolExecutor(max_workers=5) as pool:
            pending = [pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})]
            try:
                assert entered.wait(3)
                for index in range(4):
                    pending.append(pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'hybrid'}))
                    waiters(c.search._http_inference, index + 1)
                busy = api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'})
                assert busy.status_code == 503 and busy.json()['detail']['code'] == 'SEARCH_RUNTIME_BUSY'
                forbidden = api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'},
                    headers={'x-forwarded-for': '127.0.0.1'})
                assert forbidden.status_code == 403 and forbidden.json()['detail']['code'] == 'SEARCH_ACCESS_RESTRICTED'
            finally:
                release.set()
            assert [p.result(timeout=5).status_code for p in pending] == [200] * 5


def test_trusted_policy_revoked_while_queued_is_rechecked(searchable, monkeypatch):
    from data_sync_etl.api import routes

    c, _ = searchable
    entered, release = Event(), Event()
    original = c.indexing.embedding.prepare
    sessions = {'A': ACCESS, 'B': ACCESS}
    def trusted(request):
        access = sessions.get(request.cookies.get('test_session'))
        if access is None:
            raise SearchError('SEARCH_ACCESS_RESTRICTED', 401)
        return access
    def held():
        if not entered.is_set():
            entered.set()
            assert release.wait(8)
        return original()
    # Fixture of the server-side policy boundary, NOT a new production auth flow.
    monkeypatch.setattr(routes, 'search_access', trusted)
    monkeypatch.setattr(c.indexing.embedding, 'prepare', held)
    with ExitStack() as stack:
        clients = [stack.enter_context(TestClient(create_app(c), base_url='http://127.0.0.1',
            client=('127.0.0.1', 51000 + i))) for i in range(2)]
        for client, session in zip(clients, ('A', 'B'), strict=True):
            client.cookies.set('test_session', session)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(clients[0].post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})
            try:
                assert entered.wait(3)
                second = pool.submit(clients[1].post, '/api/v1/search', json={'query': 'page', 'method': 'hybrid'})
                waiters(c.search._http_inference, 1)
                del sessions['B']  # logout/revocation at the trusted test boundary
            finally:
                release.set()
            assert first.result(timeout=5).status_code == 200
            revoked = second.result(timeout=5)
            assert revoked.status_code == 401 and 'items' not in revoked.json()
        sessions['B'] = SearchAccess()  # switched account has no internal grant
        public = clients[1].post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'})
        assert public.status_code == 200 and public.json()['items'] == []
        assert clients[0].post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).json()['total'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect_kind', ['disconnect', 'task_cancel'])
async def test_cancelled_queued_http_does_not_run_model(searchable, monkeypatch, disconnect_kind):
    import asyncio

    import anyio
    from fastapi import HTTPException
    from starlette.requests import Request

    from data_sync_etl.api.routes import search_materials
    from data_sync_etl.api.search_schemas import SearchRequest

    c, _ = searchable
    entered, release = Event(), Event()
    disconnect = asyncio.Event()
    original = c.indexing.embedding.prepare
    def held():
        if not entered.is_set():
            entered.set()
            assert release.wait(8)
        return original()
    monkeypatch.setattr(c.indexing.embedding, 'prepare', held)
    app = create_app(c)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})
            try:
                assert await anyio.to_thread.run_sync(entered.wait, 3)
                async def receive():
                    await disconnect.wait()
                    return {'type': 'http.disconnect'}
                scope = {'type': 'http', 'http_version': '1.1', 'method': 'POST', 'scheme': 'http',
                    'path': '/api/v1/search', 'query_string': b'', 'root_path': '',
                    'headers': [(b'host', b'127.0.0.1')], 'client': ('127.0.0.1', 50001),
                    'server': ('127.0.0.1', 80), 'app': app}
                task = asyncio.create_task(search_materials(SearchRequest(query='page', method='hybrid'), Request(scope, receive)))
                await anyio.to_thread.run_sync(waiters, c.search._http_inference, 1)
                if disconnect_kind == 'disconnect':
                    disconnect.set()
                    with pytest.raises(HTTPException) as error:
                        await asyncio.wait_for(task, 2)
                    assert error.value.status_code == 503 and error.value.detail['code'] == 'SEARCH_TIMEOUT'
                else:
                    task.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await task
                await anyio.to_thread.run_sync(waiters, c.search._http_inference, 0)
            finally:
                release.set()
            assert await anyio.to_thread.run_sync(lambda: first.result(timeout=5).status_code) == 200
    assert c.search._http_heavy_calls._value == 16


def test_http_waiting_deadline_is_timeout_not_busy(searchable, monkeypatch):
    c, _ = searchable
    c.settings.search_timeout_seconds = 1  # test only; capacity is NOT full
    entered, release = Event(), Event()
    original, factory = c.indexing.embedding.prepare, c.search.new_http_call
    finished = []
    def recorded(*args, **kwargs):
        value, done = factory(*args, **kwargs), Event()
        finish = value.finish
        def close():
            finish()
            done.set()
        value.finish = close
        finished.append(done)
        return value
    def held():
        entered.set()
        assert release.wait(6)
        return original()
    monkeypatch.setattr(c.indexing.embedding, 'prepare', held)
    monkeypatch.setattr(c.search, 'new_http_call', recorded)
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'vector'})
            try:
                assert entered.wait(3)
                second = pool.submit(api.post, '/api/v1/search', json={'query': 'page', 'method': 'hybrid'})
                waiters(c.search._http_inference, 1)
                for pending in (first, second):
                    response = pending.result(timeout=3)
                    assert response.status_code == 503 and response.json()['detail']['code'] == 'SEARCH_TIMEOUT'
                waiters(c.search._http_inference, 0)
            finally:
                release.set()
            assert all(done.wait(3) for done in finished[:2])
        assert c.search._http_heavy_calls._value == 16
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'}).status_code == 200
