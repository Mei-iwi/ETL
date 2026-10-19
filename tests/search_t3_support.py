"""T3-only real HTTP/control/measurement tools. Never accepts a Dev URL."""

import ast
import socket
import subprocess
from collections import Counter
from contextlib import contextmanager
from threading import Event, Lock, Thread
from time import perf_counter
from types import ModuleType, SimpleNamespace

import uvicorn
from fastapi import FastAPI
from sqlalchemy import event

from data_sync_etl.api import routes
from data_sync_etl.api.search_schemas import SearchResponse
from tests.search_read_path_probe import ACTIVE, TraceMiddleware

T2_SHA = '4db021bfc981295be8974a6a206a69079d062984'
HTTP_PORTS = set()  # Registered ephemeral loopback listeners; runner audit uses this set.


def t2_control(indexing, settings, bags):
    def source(path):
        return subprocess.check_output(['git', 'show', T2_SHA + ':' + path]).decode('utf-8')
    module = ModuleType('tests._immutable_t2_search')
    exec(compile(source('src/data_sync_etl/application/search.py'), '<T2-' + T2_SHA[:7] + '>', 'exec'), module.__dict__)
    service = module.SearchService(indexing, settings, bags)
    tree = ast.parse(source('src/data_sync_etl/api/routes.py'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'search_materials')
    node.decorator_list = []
    namespace = dict(vars(routes))
    namespace['search_access'] = lambda request: routes.search_access(request)
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<T2-HTTP-route>', 'exec'), namespace)
    return service, namespace['search_materials']


class Measures:
    def __init__(self, probe):
        self.probe, self.rows, self.calls, self.done = probe, {}, {}, {}
        self.lock = Lock()
        self.active_encoders = self.max_encoders = self.encodes = 0

    def row(self):
        trace = ACTIVE.get()
        if trace is None:
            return None
        with self.lock:
            return self.rows.setdefault(trace.identifier, Counter())

    def register(self, identifier):
        with self.lock:
            self.done[identifier] = Event()

    def finish(self, identifier):
        with self.lock:
            self.done.setdefault(identifier, Event()).set()

    def take(self, identifier):
        assert self.done[identifier].wait(3), 'HTTP measurement cleanup incomplete'
        with self.lock:
            row = dict(self.rows.pop(identifier, {}))
            call = self.calls.pop(identifier, None)
            self.done.pop(identifier)
        self.probe.rows.pop(identifier)
        row.update(queue_ms=call.queue_ms if call else 0., inference_ms=call.inference_ms if call else
                   row.get('prepare_ms', 0.) + row.get('encode_ms', 0.))
        if call is not None:
            row.update(cancel_reason=call.cancel_reason, worker_closed=call._closed)
        return row

    def install(self, c, monkeypatch):
        def before(_c, _u, statement, _p, ctx, _m):
            if self.row() is not None:
                assert statement.lstrip().upper().startswith(('SELECT', 'WITH'))
                ctx.t3_started = perf_counter()
                ctx.t3_text = 'FROM content_unit_texts' in statement
        def after(_c, _u, _s, _p, ctx, _m):
            row = self.row()
            if row is not None:
                row['SQL'] += 1
                row['PG_ms'] += (perf_counter() - ctx.t3_started) * 1000
                if ctx.t3_text:
                    row['text_SELECT'] += 1
        original_start, original_success = self.probe.mongo.started, self.probe.mongo.succeeded
        def started(command):
            row = self.row()
            if row is not None:
                assert command.command_name in ('find', 'getMore', 'listCollections', 'listIndexes', 'killCursors')
                row['Mongo'] += 1
            return original_start(command)
        def succeeded(command):
            row = self.row()
            if row is not None:
                row['Mongo_ms'] += command.duration_micros / 1000
            return original_success(command)
        monkeypatch.setattr(self.probe.mongo, 'started', started)
        monkeypatch.setattr(self.probe.mongo, 'succeeded', succeeded)
        for name, key in (('prepare_query', 'prepare_ms'), ('_encode', 'encode_ms')):
            original = getattr(c.indexing.embedding, name)
            def timed(*args, original=original, key=key, **kwargs):
                row, stamp = self.row(), perf_counter()
                if key == 'encode_ms':
                    with self.lock:
                        self.active_encoders += 1
                        self.max_encoders = max(self.max_encoders, self.active_encoders)
                        self.encodes += 1
                try:
                    return original(*args, **kwargs)
                finally:
                    if row is not None:
                        row[key] += (perf_counter() - stamp) * 1000
                    if key == 'encode_ms':
                        with self.lock:
                            self.active_encoders -= 1
            monkeypatch.setattr(c.indexing.embedding, name, timed)
        original_call = c.search.new_http_call
        def recorded(*args, **kwargs):
            call = original_call(*args, **kwargs)
            call.test_finished = Event()
            finish = call.finish
            def finished():
                finish()
                call.test_finished.set()
            call.finish = finished
            trace = ACTIVE.get()
            if trace is not None:
                with self.lock:
                    self.calls[trace.identifier] = call
            return call
        monkeypatch.setattr(c.search, 'new_http_call', recorded)
        event.listen(c.engine, 'before_cursor_execute', before)
        event.listen(c.engine, 'after_cursor_execute', after)
        return lambda: (event.remove(c.engine, 'before_cursor_execute', before),
                        event.remove(c.engine, 'after_cursor_execute', after))


class Measured:
    def __init__(self, app, probe, metrics):
        self.app, self.metrics = TraceMiddleware(app, probe), metrics

    async def __call__(self, scope, receive, send):
        identifier = dict(scope.get('headers', [])).get(b'x-readpath-case')
        try:
            await self.app(scope, receive, send)
        finally:
            if identifier:
                self.metrics.finish(identifier.decode('ascii'))


@contextmanager
def http_server(endpoint, container, probe, metrics):
    # Only the SAME Search API on a temporary server, not a new production API.
    app = FastAPI()
    app.state.container = container
    app.add_api_route('/api/v1/search', endpoint, methods=['POST'], response_model=SearchResponse)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    assert port not in (8000, 8001, 5432, 5433, 27017, 27018)
    HTTP_PORTS.add(port)
    ready = Event()
    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)
            ready.set()
    server = ReadyServer(uvicorn.Config(Measured(app, probe, metrics), host='127.0.0.1', port=port,
        log_level='error', access_log=False, proxy_headers=False, lifespan='off'))
    thread = Thread(target=lambda: server.run(sockets=[listener]), name='owned-t3-http', daemon=True)
    thread.start()
    try:
        assert ready.wait(8) and server.started
        yield 'http://127.0.0.1:' + str(port)
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        HTTP_PORTS.discard(port)
        assert not thread.is_alive(), 'Owned HTTP test server did not stop'


def control_container(c, service):
    return SimpleNamespace(settings=c.settings, search=service)


def thread_counts():
    import ctypes
    import os
    import threading
    from ctypes import wintypes

    result = {'python_threads': threading.active_count()}
    if os.name != 'nt':
        result['native_threads'] = None
        return result
    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD), ('id', wintypes.DWORD),
                    ('owner', wintypes.DWORD), ('base', wintypes.LONG), ('delta', wintypes.LONG), ('flags', wintypes.DWORD)]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    snapshot = kernel.CreateToolhelp32Snapshot
    snapshot.argtypes, snapshot.restype = [wintypes.DWORD, wintypes.DWORD], wintypes.HANDLE
    first, next_entry, close = kernel.Thread32First, kernel.Thread32Next, kernel.CloseHandle
    for function in (first, next_entry):
        function.argtypes, function.restype = [wintypes.HANDLE, ctypes.POINTER(Entry)], wintypes.BOOL
    close.argtypes, close.restype = [wintypes.HANDLE], wintypes.BOOL
    handle = snapshot(4, 0)
    assert handle != ctypes.c_void_p(-1).value
    try:
        entry, count = Entry(), 0
        entry.size = ctypes.sizeof(entry)
        valid = first(handle, ctypes.byref(entry))
        while valid:
            count += entry.owner == os.getpid()
            valid = next_entry(handle, ctypes.byref(entry))
        result['native_threads'] = count
    finally:
        assert close(handle)
    return result
