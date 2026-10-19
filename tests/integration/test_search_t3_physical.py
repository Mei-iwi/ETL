"""T3 owned PG/Mongo + real MiniLM384 + independent real HTTP clients."""

import hashlib
import itertools
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier, Event
from time import perf_counter, process_time

import httpx
import pytest
from sqlalchemy import delete

from data_sync_etl.api import routes
from data_sync_etl.db.models import MasterLearningResource, ResourceVersion
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.integration.test_search_t1_physical import container as container
from tests.integration.test_search_t1_physical import physical_search as physical_search
from tests.integration.test_search_t1_recovery import seed_medium
from tests.search_read_path_probe import ACTIVE, summary
from tests.search_t3_support import (
    T2_SHA,
    Measures,
    control_container,
    http_server,
    t2_control,
)
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_T1_PHYSICAL') != '1', reason='Owned isolation runner required')]


def hashes():
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in (
        'src/data_sync_etl/application/search.py', 'src/data_sync_etl/application/search_runtime.py',
        'src/data_sync_etl/api/routes.py')}


@pytest.fixture
def real_http(physical_search, monkeypatch):
    c, first, db = physical_search
    versions = seed_medium(c, first, db)
    assert c.settings.search_timeout_seconds == 15 and not c.settings.search_query_embedding_cache_enabled
    baseline, endpoint = t2_control(c.indexing, c.settings, c.indexing.bags)
    metrics = Measures(c._t1_probe)
    cleanup = metrics.install(c, monkeypatch)
    serial = itertools.count()
    with ExitStack() as stack:
        urls = {'T2': stack.enter_context(http_server(endpoint, control_container(c, baseline), c._t1_probe, metrics)),
                'T3': stack.enter_context(http_server(routes.search_materials, c, c._t1_probe, metrics))}
        clients = {path: [stack.enter_context(httpx.Client(base_url=url, timeout=18, trust_env=False))
                         for _ in range(6)] for path, url in urls.items()}
        for path in clients:
            for i, client in enumerate(clients[path]):
                client.cookies.set('test_session', 'client-' + str(i))
        def request(path, client=0, method='hybrid', case='request', client_timeout=None, **body):
            identifier = case + '-' + str(next(serial))
            metrics.register(identifier)
            stamp = perf_counter()
            response = clients[path][client].post('/api/v1/search',
                json={'query': 'Page fractions addition', 'method': method, **body},
                headers={'x-readpath-case': identifier, 'x-readpath-profile': '0'},
                **({'timeout': client_timeout} if client_timeout is not None else {}))
            elapsed = (perf_counter() - stamp) * 1000
            measured = metrics.take(identifier)
            code = response.json().get('detail', {}).get('code') if response.status_code != 200 else None
            return response, {'status': response.status_code, 'error_code': code, 'wall_ms': elapsed, **measured}
        try:
            yield c, first, db, versions, baseline, request, clients, metrics
        finally:
            cleanup()


@dataclass(frozen=True)
class FixtureGrant(SearchAccess):
    resources: frozenset[str] = frozenset()

    def allows(self, resource, version, unit):
        return resource['id'] in self.resources and super().allows(resource, version, unit)


def test_t3_targeted(real_http, monkeypatch):
    c, first, db, versions, baseline, request, _clients, metrics = real_http
    target = Path(os.environ['ETL_T1_OUTPUT'])
    report = {'status': 'RUNNING', 'baseline': T2_SHA, 'source_hashes': hashes(),
        'static_pairs': [], 'reproduction': [], 'scoped_grants': [], 'races': [],
        'network_disconnects': [], 'queued_revocation': False, 'browser_E2E': 'NOT_RUN'}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    for method in ('bow', 'vector', 'hybrid'):
        for page in (1, 2):
            left, old = request('T2', method=method, page=page, size=2)
            right, new = request('T3', method=method, page=page, size=2)
            assert left.status_code == right.status_code == 200 and left.content == right.content
            assert [(r['SQL'], r['text_SELECT'], r['Mongo']) for r in (old, new)] == [(110, 12, 67)] * 2
            report['static_pairs'].append({'method': method, 'page': page, 'T2': old, 'T3': new})
        save()
    for method, other in (('vector', 'vector'), ('hybrid', 'hybrid'), ('vector', 'hybrid')):
        for path, service in (('T2', baseline), ('T3', c.search)):
            entered, release = Event(), Event()
            original = service._metadata
            def held(*args, original=original, entered=entered, release=release, **kwargs):
                trace = ACTIVE.get()
                if trace is not None and trace.identifier.startswith('held-first') and not entered.is_set():
                    entered.set()
                    assert release.wait(8)
                return original(*args, **kwargs)
            with monkeypatch.context() as patch:
                patch.setattr(service, '_metadata', held)
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first_request = pool.submit(request, path, 0, method, 'held-first')
                    try:
                        assert entered.wait(5)
                        rival, observed = request(path, 1, other)
                        assert rival.status_code == (503 if path == 'T2' else 200)
                        assert observed['error_code'] == ('SEARCH_RUNTIME_BUSY' if path == 'T2' else None)
                        bow, _ = request(path, 2, 'bow')
                        assert bow.status_code == 200
                    finally:
                        release.set()
                    assert first_request.result(timeout=6)[0].status_code == 200
                report['reproduction'].append({'path': path, 'method': method, 'rival': other, **observed})
                save()

    original_access = routes.search_access
    first_half = frozenset(v['resource_id'] for v in versions[:3])
    second_half = frozenset(v['resource_id'] for v in versions[3:])
    sessions = {'client-0': FixtureGrant('local_demo', first_half), 'client-1': FixtureGrant('local_demo', second_half)}
    def trusted(request):
        original_access(request)  # keep real loopback/origin/proxy restrictions
        grant = sessions.get(request.cookies.get('test_session'))
        if grant is None:
            raise SearchError('SEARCH_ACCESS_RESTRICTED', 401)
        return grant
    with monkeypatch.context() as patch:
        patch.setattr(routes, 'search_access', trusted)
        for method in ('bow', 'vector', 'hybrid'):
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = [pool.submit(request, 'T3', i, method, 'scoped') for i in range(2)]
                rows = [job.result(timeout=10) for job in jobs]
            for i, (response, row) in enumerate(rows):
                assert response.status_code == 200 and response.json()['total'] == 3
                assert {item['resource_id'] for item in response.json()['items']} <= (first_half if i == 0 else second_half)
                control, _ = request('T2', i, method, 'scoped-control')
                assert response.content == control.content
                report['scoped_grants'].append({'client': i, 'method': method, **row})
        entered, release = Event(), Event()
        original = c.indexing.embedding._encode
        def held_encode(*args, original=original, entered=entered, release=release, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(8)
            return original(*args, **kwargs)
        patch.setattr(c.indexing.embedding, '_encode', held_encode)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first_request = pool.submit(request, 'T3', 0, 'vector')
            try:
                assert entered.wait(5)
                waiting = pool.submit(request, 'T3', 1, 'hybrid')
                queue = c.search._http_inference
                with queue._condition:
                    assert queue._condition.wait_for(lambda: len(queue._queue) == 1, timeout=4)
                del sessions['client-1']
            finally:
                release.set()
            assert first_request.result(timeout=5)[0].status_code == 200
            assert waiting.result(timeout=5)[0].status_code == 401
        report['queued_revocation'] = True
        assert request('T3', 1, 'vector')[0].status_code == 401
        sessions['client-1'] = SearchAccess()
        assert request('T3', 1, 'hybrid')[0].json()['items'] == []
        assert request('T3', 0, 'bow')[0].json()['total'] == 3
    save()

    for phase in ('queued', 'running'):
        entered, release = Event(), Event()
        original = c.indexing.embedding._encode
        def held_encode(*args, original=original, entered=entered, release=release, **kwargs):
            if not entered.is_set():
                entered.set()
                assert release.wait(8)
            return original(*args, **kwargs)
        with monkeypatch.context() as patch:
            patch.setattr(c.indexing.embedding, '_encode', held_encode)
            with ThreadPoolExecutor(max_workers=2) as pool:
                active = pool.submit(request, 'T3', 0, 'vector', 'network-holder',
                    client_timeout=.25 if phase == 'running' else None)
                waiting = None
                try:
                    assert entered.wait(5)
                    if phase == 'queued':
                        waiting = pool.submit(request, 'T3', 1, 'hybrid', 'network-abort', client_timeout=.25)
                        with c.search._http_inference._condition:
                            assert c.search._http_inference._condition.wait_for(lambda: len(c.search._http_inference._queue) == 1, timeout=3)
                    victim = waiting if phase == 'queued' else active
                    with pytest.raises(httpx.ReadTimeout):
                        victim.result(timeout=3)
                    prefix = 'network-abort' if phase == 'queued' else 'network-holder'
                    identifier = next(k for k in metrics.calls if k.startswith(prefix))
                    cancelled = metrics.calls[identifier]
                    measured = metrics.take(identifier)
                    assert cancelled.cancelled.is_set() and cancelled.cancel_reason == 'disconnect'
                    if phase == 'queued':
                        assert cancelled.test_finished.wait(3)
                        with c.search._http_inference._condition:
                            assert not c.search._http_inference._queue
                    else:
                        assert not cancelled.test_finished.is_set() and c.search._http_inference._running
                        waiting = pool.submit(request, 'T3', 1, 'hybrid')
                        with c.search._http_inference._condition:
                            assert c.search._http_inference._condition.wait_for(lambda: len(c.search._http_inference._queue) == 1, timeout=3)
                finally:
                    release.set()
                survivor = active if phase == 'queued' else waiting
                assert survivor.result(timeout=6)[0].status_code == 200
                assert cancelled.test_finished.wait(3)
                measured.update(queue_ms_after_completion=cancelled.queue_ms,
                                inference_ms_after_completion=cancelled.inference_ms,
                                worker_closed_after_completion=cancelled._closed)
        report['network_disconnects'].append({'phase': phase, 'client_error': 'READ_TIMEOUT', **measured})
        save()

    with c.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': first['id']}, order=('sequence_no', 'id'))
        run = c.indexing._runs(repo, first['id'])[0]
        resource = repo.get('master_learning_resources', first['resource_id'])
        version = repo.get('resource_versions', first['id'])
    def mutate(case):
        if case in ('pg_text', 'revoked', 'index'):
            table, key, change = {
                'pg_text': ('content_unit_texts', units[-1]['id'], {'text': 'Owned T3 changed text'}),
                'revoked': ('master_learning_resources', first['resource_id'], {'publication_status': 'DRAFT'}),
                'index': ('etl_stage_runs', run['id'], {'status': 'PENDING'})}[case]
            with c.uow() as repo:
                old = repo.get(table, key)
                repo.update(table, key, change)
            def undo():
                with c.uow() as repo:
                    repo.update(table, key, {k: old[k] for k in change})
            return undo
        if case in ('bag', 'mongo_unit'):
            collection = db.content_bags if case == 'bag' else db.mongo_content_units
            key = units[0]['id'] if case == 'bag' else units[-1]['id']
            old = collection.find_one({'_id': key})
            change = {'keywords': ['changed'], 'embedding_text': 'changed'} if case == 'bag' else {'text': 'Mongo changed'}
            collection.update_one({'_id': key}, {'$set': change})
            return lambda: collection.replace_one({'_id': key}, old)
        with c.uow() as repo:
            repo.insert('master_learning_resources', {**resource, 'id': 't3-global-r', 'mongo_id': None})
            repo.insert('resource_versions', {**version, 'id': 't3-global-v', 'resource_id': 't3-global-r', 'mongo_id': None})
        def undo():
            with c.uow() as repo:
                repo.session.execute(delete(ResourceVersion).where(ResourceVersion.id == 't3-global-v'))
                repo.session.execute(delete(MasterLearningResource).where(MasterLearningResource.id == 't3-global-r'))
        return undo
    for method in ('bow', 'vector', 'hybrid'):
        for case in ('pg_text', 'revoked', 'index', 'bag', 'mongo_unit', 'global'):
            results = []
            for path, service in (('T2', baseline), ('T3', c.search)):
                entered, release = Event(), Event()
                original, reads = service._metadata, [0]
                def final(*args, original=original, reads=reads, entered=entered, release=release, **kwargs):
                    reads[0] += 1
                    if reads[0] == 2:
                        entered.set()
                        assert release.wait(8)
                    return original(*args, **kwargs)
                with monkeypatch.context() as patch:
                    patch.setattr(service, '_metadata', final)
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        pending = pool.submit(request, path, 0, method, 'race-' + case)
                        def undo():
                            pass
                        try:
                            assert entered.wait(5)
                            undo = mutate(case)
                            release.set()
                            response, row = pending.result(timeout=8)
                            if response.status_code == 200:
                                assert response.json()['total'] == 5
                                assert first['resource_id'] not in {r['resource_id'] for r in response.json()['items']}
                            else:
                                assert response.status_code == 409 and row['error_code'] == 'SEARCH_INDEX_STALE'
                            results.append((response.status_code, response.content))
                        finally:
                            release.set()
                            undo()
            assert results[0] == results[1]
            report['races'].append({'method': method, 'case': case, 'status': results[0][0]})
            save()
    assert metrics.max_encoders == 1 and metrics.active_encoders == 0
    report['max_encoders'] = metrics.max_encoders
    report['status'] = 'TARGETED_PASS'
    save()


def test_t3_benchmark(real_http):
    from collections import Counter

    from tests.integration.test_http_query_cache_pg import memory
    from tests.search_t1_support import active_host_seconds
    from tests.search_t3_support import thread_counts

    c, _first, _db, _versions, _baseline, request, _clients, metrics = real_http
    target = Path(os.environ['ETL_T1_OUTPUT'])
    report = {'status': 'RUNNING', 'baseline': T2_SHA, 'source_hashes': hashes(),
        'cache': 'OFF', 'model_dimension': 384, 'timeout_seconds': 15,
        'waves_per_group': 30, 'samples': [], 'waves': [], 'groups': []}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    model = c.indexing.embedding._model
    references = {}
    def body(pattern):
        return {'query': 'Page fractions addition learning ' + str(pattern),
                'page': 2 if pattern == 1 else 1, 'size': 10 if pattern == 2 else 2}
    for method in ('bow', 'vector', 'hybrid'):
        for pattern in range(3):
            response, _ = request('T2', method=method, **body(pattern))
            assert response.status_code == 200 and response.json()['total'] == 6
            references[method, pattern] = response.content
    for count in (1, 2, 3, 5):
        with ThreadPoolExecutor(max_workers=count) as pool:
            for method in ('bow', 'vector', 'hybrid'):
                for wave in range(30):
                    for path in (('T2', 'T3') if wave % 2 == 0 else ('T3', 'T2')):
                        start = Barrier(count)
                        pattern = wave % 3
                        def run(client, path=path, method=method, pattern=pattern, start=start):
                            start.wait(timeout=10)
                            stamp = perf_counter()
                            try:
                                return request(path, client, method, 'bench', **body(pattern))
                            except httpx.RequestError as exc:
                                # Not an HTTP status/response: retain separately, never fabricate a 503.
                                return None, {'status': 0, 'error_code': 'NETWORK_TIMEOUT' if isinstance(exc, httpx.TimeoutException)
                                    else 'NETWORK_ERROR', 'wall_ms': (perf_counter() - stamp) * 1000}
                        stamp, cpu, active = perf_counter(), process_time(), active_host_seconds()
                        before_threads = thread_counts()
                        jobs = [pool.submit(run, client) for client in range(count)]
                        rows = [job.result(timeout=20) for job in jobs]
                        wall_ms, cpu_ms = (perf_counter() - stamp) * 1000, (process_time() - cpu) * 1000
                        active_ms = (active_host_seconds() - active) * 1000 if active is not None else None
                        wave_row = {'clients': count, 'method': method, 'path': path, 'wave': wave,
                            'wall_ms': wall_ms, 'cpu_ms': cpu_ms, 'active_ms': active_ms,
                            'threads_before': before_threads, **thread_counts(), **memory()}
                        wave_row['success'] = sum(r['status'] == 200 for _, r in rows)
                        report['waves'].append(wave_row)
                        for client, (response, measured) in enumerate(rows):
                            parity = (response.content == references[method, pattern]
                                      if response is not None and response.status_code == 200 else None)
                            report['samples'].append({'clients': count, 'client': client, 'method': method,
                                'path': path, 'wave': wave, 'parity': parity, **measured})
                        save()  # retain failures/outliers BEFORE assertions
                        assert all(r['status'] in (200, 503) for _, r in rows)
                        for response, measured in rows:
                            if measured['status'] == 200:
                                assert response.content == references[method, pattern]
                                assert (measured['SQL'], measured['text_SELECT'], measured['Mongo']) == (110, 12, 67)
                            else:
                                assert measured['error_code'] == 'SEARCH_RUNTIME_BUSY', measured
                        if path == 'T3' or method == 'bow':
                            assert all(r['status'] == 200 for _, r in rows), 'Within-capacity workload failed'
                        if active_ms is not None:
                            assert wall_ms - active_ms < 1000, 'Host suspend contaminated measured wave'
                print(f'T3_BENCHMARK_30_WAVES clients={count} method={method}', flush=True)
    for count in (1, 2, 3, 5):
        for method in ('bow', 'vector', 'hybrid'):
            for path in ('T2', 'T3'):
                rows = [r for r in report['samples'] if (r['clients'], r['method'], r['path']) == (count, method, path)]
                waves = [r for r in report['waves'] if (r['clients'], r['method'], r['path']) == (count, method, path)]
                success = [r for r in rows if r['status'] == 200]
                assert len(success) >= 30
                wall_ms = sum(r['wall_ms'] for r in waves)
                report['groups'].append({'clients': count, 'method': method, 'path': path,
                    'attempts': len(rows), 'successful': len(success),
                    'statuses': dict(Counter(str(r['status']) + ':' + str(r['error_code']) for r in rows)),
                    'all_latency_ms': summary([r['wall_ms'] for r in rows]),
                    'success_latency_ms': summary([r['wall_ms'] for r in success]),
                    'queue_ms': summary([r.get('queue_ms', 0) for r in success]),
                    'inference_ms': summary([r.get('inference_ms', 0) for r in success]),
                    'cpu_wave_ms': summary([r['cpu_ms'] for r in waves]),
                    'cpu_percent': sum(r['cpu_ms'] for r in waves) / wall_ms * 100,
                    'rss_bytes': summary([r['rss_bytes'] for r in waves]),
                    'native_threads': summary([r['native_threads'] for r in waves]),
                    'python_threads': summary([r['python_threads'] for r in waves]),
                    'throughput_success_rps': len(success) / wall_ms * 1000,
                    'parity': all(r['parity'] for r in success)})
    assert c.indexing.embedding._model is model and metrics.max_encoders == 1 and metrics.active_encoders == 0
    report.update(status='BENCHMARK_PASS', model_reused=True, max_encoders=metrics.max_encoders)
    save()
