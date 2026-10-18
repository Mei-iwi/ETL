"""Opt-in test probes. No raw statements, parameters, queries or documents retained."""

import hashlib
import hmac
import inspect
import json
import os
import re
import secrets
import statistics
from collections import Counter
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from time import perf_counter, process_time

from bson import BSON
from pymongo import monitoring
from sqlalchemy import event

ACTIVE = ContextVar('readpath_probe', default=None)
ROOT = Path(__file__).resolve().parents[1]
SOURCE_PREFIX = str(ROOT / 'src') + os.sep


def summary(values):
    ordered = sorted(values)
    if not ordered:
        return {'n': 0}
    position = (len(ordered) - 1) * .95
    lo = int(position)
    return {'n': len(ordered), 'min': ordered[0], 'max': ordered[-1],
            'p50': statistics.median(ordered),
            'p95': ordered[lo] + (ordered[min(lo + 1, len(ordered) - 1)] - ordered[lo]) * (position - lo),
            'stddev': statistics.pstdev(ordered)}


def shape(value):
    if isinstance(value, dict):
        return {str(k): shape(v) for k, v in sorted(value.items())
                if k not in ('$db', 'lsid', '$clusterTime')}
    if isinstance(value, (list, tuple)):
        return {'array_length': len(value), 'types': sorted({type(v).__name__ for v in value})}
    return type(value).__name__


def callsites():
    frame = inspect.currentframe().f_back
    result = []
    try:
        while frame is not None:
            filename = frame.f_code.co_filename
            if filename.startswith(SOURCE_PREFIX):
                relative = filename[len(str(ROOT)) + 1:].replace('\\', '/')
                result.append(f'{relative}:{frame.f_lineno}:{frame.f_code.co_name}')
            frame = frame.f_back
    finally:
        del frame
    return result[:12]


class Trace:
    def __init__(self, identifier, enabled):
        self.identifier, self.enabled = identifier, enabled
        self.operations, self.logical, self.phases, self.stack = [], [], Counter(), []
        self.candidate_count = self.hydration_count = self.snapshot_units = 0
        self.returned_at = None


class Probe:
    def __init__(self):
        self.secret = secrets.token_bytes(32)
        self.rows = {}
        self.mongo = MongoCommands(self)

    def key(self, value):
        # Random audit-run HMAC prevents retaining enumerable raw parameters.
        encoded = json.dumps(value, default=str, sort_keys=True).encode()
        return hmac.new(self.secret, encoded, hashlib.sha256).hexdigest()

    def wrap(self, original, category, *, result=None, logical=False, returned=False):
        @wraps(original)
        def wrapped(*args, **kwargs):
            trace = ACTIVE.get()
            if trace is None:
                return original(*args, **kwargs)
            if not trace.enabled:
                value = original(*args, **kwargs)
                if returned:
                    trace.returned_at = perf_counter()
                return value
            name = category(*args, **kwargs) if callable(category) else category
            trace.stack.append(name)
            started = perf_counter()
            value = None
            failed = True
            try:
                value = original(*args, **kwargs)
                failed = False
                if result:
                    result(trace, value)
                return value
            finally:
                elapsed = (perf_counter() - started) * 1000
                trace.phases[name] += elapsed
                assert trace.stack.pop() == name
                if logical:
                    trace.logical.append({'operation': name, 'ms_inclusive': elapsed,
                                          'result_count': len(value) if isinstance(value, list) else int(value is not None),
                                          'failed': failed})
                if returned:
                    trace.returned_at = perf_counter()
        return wrapped

    def install(self, monkeypatch, engine):
        from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
        from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
        from data_sync_etl.adapters.persistence.search_reader import SearchRepositoryMixin
        from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
        from data_sync_etl.api import routes
        from data_sync_etl.application import search
        from data_sync_etl.application.indexing import IndexingService

        targets = [
            (routes, 'search_access', 'access_check', {}),
            (search.SearchService, 'search_http', 'search_http', {'returned': True}),
            (search.SearchService, '_allowed_units', 'allowed_units', {}),
            (search.SearchService, '_ready_snapshot', 'ready_snapshot', {}),
            (search.SearchService, '_snippets', 'snippets', {'result': lambda t, r: setattr(t, 'hydration_count', t.hydration_count + len(r[1]))}),
            (IndexingService, '_snapshot', 'snapshot', {'result': lambda t, r: setattr(t, 'snapshot_units', t.snapshot_units + len(r[2]))}),
            (IndexingService, '_verified', 'verified', {}),
            (MongoBagStore, 'ensure_ready', 'schema_check', {}),
            (MongoBagStore, 'search_candidates', 'bag_candidates', {'result': lambda t, r: setattr(t, 'candidate_count', len(r)), 'logical': True}),
            (MongoBagStore, 'get_bag', 'get_bag', {'logical': True}),
            (MongoContentReader, 'units', 'mongo_units', {'logical': True}),
            (MongoContentReader, 'content_fingerprint', 'mongo_fingerprint', {'logical': True}),
            (SQLAlchemyRepository, 'get', lambda _r, table, *_a, **_k: 'repo_get:' + table, {'logical': True}),
            (SQLAlchemyRepository, 'find', lambda _r, table, *_a, **_k: 'repo_find:' + table, {'logical': True}),
            (SearchRepositoryMixin, 'search_versions', 'metadata', {'logical': True}),
            (LocalSentenceEmbedding, 'prepare_query', 'model_prepare', {}),
            (LocalSentenceEmbedding, '_encode', lambda m, *_a: 'query_encode' if m._checked else 'readiness_probe', {}),
            (search, 'binary_score', 'scoring', {}), (search, 'cosine', 'scoring', {}),
            (search, 'rrf', 'ranking', {}), (search, 'encode_context', 'ranking', {}),
        ]
        # Wrapping globals sorted affects only this module, never the interpreter.
        monkeypatch.setattr(search, 'sorted', self.wrap(sorted, 'ranking'), raising=False)
        for target, method, category, options in targets:
            monkeypatch.setattr(target, method, self.wrap(getattr(target, method), category, **options))

        def before(_conn, _cursor, statement, params, ctx, _many):
            trace = ACTIVE.get()
            if trace is None or not trace.enabled:
                return
            if not statement.lstrip().upper().startswith(('SELECT', 'WITH')):
                raise AssertionError('Measured Search attempted a write/DDL')
            canonical = re.sub(r'\s+', ' ', statement).strip()
            tables = sorted(set(re.findall(r'(?:FROM|JOIN)\s+([a-z_]+)', canonical, re.I)))
            item = {
                'backend': 'pg', 'operation': 'SELECT', 'tables': tables,
                'shape': hashlib.sha256(canonical.encode()).hexdigest(),
                'exact': self.key([canonical, params]), 'phase': list(trace.stack), 'callsites': callsites()}
            ctx.readpath = (trace, perf_counter(), item)

        def after(_conn, cursor, _statement, _params, ctx, _many):
            observed = getattr(ctx, 'readpath', None)
            if observed:
                trace, started, item = observed
                item.update(ms=(perf_counter() - started) * 1000, driver_rowcount=cursor.rowcount)
                trace.operations.append(item)

        event.listen(engine, 'before_cursor_execute', before)
        event.listen(engine, 'after_cursor_execute', after)
        return lambda: (event.remove(engine, 'before_cursor_execute', before),
                        event.remove(engine, 'after_cursor_execute', after))


class MongoCommands(monitoring.CommandListener):
    def __init__(self, probe):
        self.probe = probe
        self.pending = {}

    def started(self, event):
        trace = ACTIVE.get()
        if trace is None or not trace.enabled:
            return
        if event.command_name not in ('find', 'getMore', 'listCollections', 'listIndexes', 'killCursors'):
            raise AssertionError('Measured Search attempted unexpected Mongo command')
        command = event.command
        collection = (command.get('collection') if event.command_name == 'getMore'
                      else command.get(event.command_name))
        self.pending[(event.connection_id, event.request_id)] = (trace, {
            'backend': 'mongo', 'operation': event.command_name,
            'collection': collection if isinstance(collection, str) else 'catalog',
            'shape': hashlib.sha256(json.dumps(shape(command), sort_keys=True).encode()).hexdigest(),
            'exact': self.probe.key({k: v for k, v in command.items() if k not in ('lsid', '$clusterTime', '$db')}),
            'phase': list(trace.stack), 'callsites': callsites()})

    def succeeded(self, event):
        observed = self.pending.pop((event.connection_id, event.request_id), None)
        if observed:
            trace, item = observed
            cursor = event.reply.get('cursor', {})
            batch = cursor.get('firstBatch', cursor.get('nextBatch', []))
            # Size/count only; no reply/payload survives the callback.
            item.update(ms=event.duration_micros / 1000, returned_docs=len(batch),
                        returned_bson_bytes=sum(len(BSON.encode(doc)) for doc in batch), failed=False)
            trace.operations.append(item)

    def failed(self, event):
        observed = self.pending.pop((event.connection_id, event.request_id), None)
        if observed:
            trace, item = observed
            item.update(ms=event.duration_micros / 1000, failed=True)
            trace.operations.append(item)


class TraceMiddleware:
    def __init__(self, app, probe):
        self.app, self.probe = app, probe

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['path'] != '/api/v1/search':
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        identifier = headers[b'x-readpath-case'].decode('ascii')
        trace = Trace(identifier, headers.get(b'x-readpath-profile') == b'1')
        token = ACTIVE.set(trace)
        started, cpu = perf_counter(), process_time()
        code, digest = 0, hashlib.sha256()

        async def sending(message):
            nonlocal code
            if message['type'] == 'http.response.start':
                code = message['status']
            if message['type'] == 'http.response.body':
                digest.update(message.get('body', b''))
            await send(message)

        try:
            await self.app(scope, receive, sending)
        finally:
            finished = perf_counter()
            operations = trace.operations
            pg = [v for v in operations if v['backend'] == 'pg']
            mongo = [v for v in operations if v['backend'] == 'mongo']
            self.probe.rows[identifier] = {
                'span_id': identifier, 'profiler': trace.enabled,
                'total_ms': (finished - started) * 1000, 'cpu_ms': (process_time() - cpu) * 1000,
                'access_check_ms': trace.phases['access_check'],
                'PG_exec_count': len(pg), 'PG_exec_ms': sum(v['ms'] for v in pg),
                'Mongo_cmd_count': len(mongo), 'Mongo_cmd_ms': sum(v['ms'] for v in mongo),
                'candidate_count': trace.candidate_count, 'hydration_count': trace.hydration_count,
                'snapshot_unit_visits': trace.snapshot_units,
                'embed_ms': trace.phases['query_encode'], 'model_prepare_ms': trace.phases['model_prepare'],
                'score_rank_ms': trace.phases['scoring'] + trace.phases['ranking'],
                'serialize_ms': (finished - trace.returned_at) * 1000 if trace.returned_at else None,
                'response_status': code, 'response_hash': digest.hexdigest(),
                'cache_status': 'OFF', 'phases_inclusive_ms': dict(trace.phases),
                'logical': trace.logical, 'operations': operations}
            ACTIVE.reset(token)
