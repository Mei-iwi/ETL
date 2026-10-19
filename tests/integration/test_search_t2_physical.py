"""T2 physical parity/races/benchmarks; only the owned T1 isolation runner."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore
from data_sync_etl.application.search import SearchService
from data_sync_etl.db.models import MasterLearningResource, ResourceVersion
from data_sync_etl.main import create_app
from tests.integration.test_search_t1_physical import container as container
from tests.integration.test_search_t1_physical import physical_search as physical_search
from tests.integration.test_search_t1_recovery import seed_medium
from tests.search_read_path_probe import ACTIVE, TraceMiddleware
from tests.search_t1_support import FailureCheckpoint
from tests.search_t2_support import T1_SHA, command_summary, t1_control
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_T1_PHYSICAL') != '1', reason='Owned isolated runner required')]


@pytest.fixture
def t2_fixture(physical_search, monkeypatch):
    c, first, db = physical_search
    versions = seed_medium(c, first, db)
    probe = c._t1_probe
    baseline = t1_control(probe, monkeypatch)(c.indexing, c.settings, c.indexing.bags)
    current = MongoBagStore.search_bags
    monkeypatch.setattr(MongoBagStore, 'search_bags', probe.wrap(current, 'search_bags'))
    return c, first, db, versions, baseline, SearchService(c.indexing, c.settings, c.indexing.bags)


def test_t2_targeted(t2_fixture, monkeypatch):
    c, first, db, versions, baseline, candidate = t2_fixture
    target = Path(os.environ['ETL_T1_OUTPUT'])
    report = {'status': 'RUNNING', 'T1': T1_SHA, 'source_hashes': source_hashes(),
              'static_pairs': 0, 'races': [], 'counts': [], 'concurrency': []}
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    probe, serial = c._t1_probe, 0
    with c.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': first['id']}, order=('sequence_no', 'id'))
        units2 = repo.find('content_units', {'resource_version_id': versions[1]['id']})
        resource = repo.get('master_learning_resources', first['resource_id'])
        source_version = repo.get('resource_versions', first['id'])
        stage = c.indexing._runs(repo, first['id'])[0]
        subject = repo.find('resource_subjects', {'resource_id': first['resource_id']})[0]['subject_id']
        grade = repo.find('resource_grade_levels', {'resource_id': first['resource_id']})[0]['grade_level_id']

    def mutate(case):
        undo = []
        def pg(table, key, values):
            with c.uow() as repo:
                row = repo.get(table, key)
                old = {name: row[name] for name in values}
                repo.update(table, key, values)
            def restore():
                with c.uow() as repo:
                    repo.update(table, key, old)
            undo.append(restore)
        if case in ('pg_text', 'pg_mongo', 'two_versions'):
            pg('content_unit_texts', units[-1]['id'], {'text': 'Changed owned T2 text'})
        if case == 'two_versions':
            pg('content_unit_texts', units2[-1]['id'], {'text': 'Changed owned second version'})
        if case == 'revoked':
            pg('master_learning_resources', first['resource_id'], {'publication_status': 'DRAFT'})
        if case == 'rejected':
            pg('content_units', units[0]['id'], {'review_status': 'REJECTED'})
        if case == 'index':
            pg('etl_stage_runs', stage['id'], {'status': 'PENDING'})
        if case == 'global':
            with c.uow() as repo:
                repo.insert('master_learning_resources', {**resource, 'id': 't2-new-resource', 'mongo_id': None})
                repo.insert('resource_versions', {**source_version, 'id': 't2-new-version',
                    'resource_id': 't2-new-resource', 'mongo_id': None})
            def remove():
                with c.uow() as repo:
                    repo.session.execute(delete(ResourceVersion).where(ResourceVersion.id == 't2-new-version'))
                    repo.session.execute(delete(MasterLearningResource).where(MasterLearningResource.id == 't2-new-resource'))
            undo.append(remove)
        if case in ('bag', 'delete', 'inactive', 'binding', 'model', 'vector', 'unit', 'pg_mongo',
                    'legacy', 'acl', 'missing_required', 'wrong_type'):
            collection = db.mongo_content_units if case in ('unit', 'pg_mongo') else db.content_bags
            key = units[-1]['id'] if case in ('unit', 'pg_mongo') else units[0]['id']
            old = collection.find_one({'_id': key})
            changes = {
                'bag': {'keywords': ['changed'], 'embedding_text': 'changed'},
                'inactive': {'is_current': False}, 'binding': {'resource_version_id': 'outside-authorized-version'},
                'model': {'embedding_model': 'different@revision'},
                'vector': {'embedding': [-v for v in old.get('embedding', [])]},
                'unit': {'text': 'Mongo-only changed'}, 'pg_mongo': {'text': 'Changed owned T2 text'},
                'legacy': {'legacy_content': 'untrusted'}, 'acl': {'acl_denied': True},
                'wrong_type': {'keywords': {'invalid': 'object'}},
            }
            if case == 'delete':
                collection.delete_one({'_id': key})
            elif case == 'missing_required':
                collection.update_one({'_id': key}, {'$unset': {'embedding': ''}}, bypass_document_validation=True)
            else:
                # Malformed documents model legacy/external writers ONLY in owned test DB.
                collection.update_one({'_id': key}, {'$set': changes[case]},
                    bypass_document_validation=case in ('legacy', 'acl', 'wrong_type'))
            def restore_document(collection=collection, old=old):
                collection.replace_one({'_id': old['_id']}, old, upsert=True)
            undo.append(restore_document)
        def restore_all():
            for operation in reversed(undo):
                operation()
        return restore_all

    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        def request(service, method='bow', **options):
            nonlocal serial
            c.search = service
            serial += 1
            identifier = 't2-target-' + str(serial)
            response = api.post('/api/v1/search', json={'query': 'Page', 'method': method, **options},
                headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
            row = probe.rows.pop(identifier)
            failures.observe(response, method=method, request_id=serial, duration_ms=row['total_ms'], fixture='medium')
            return response, row
        for method in ('bow', 'vector', 'hybrid'):
            options = [{'page': page, 'size': size} for page in (1, 2, 1, 101) for size in (1, 10, 50)]
            options += [{'filters': {key: value}} for key, value in (
                ('subject_id', subject), ('grade_level_id', grade), ('resource_id', first['resource_id']),
                ('resource_version_id', first['id']), ('resource_type_code', resource['resource_type_code']),
                ('resource_id', 'absent-resource'))]
            for option in options:
                left, a = request(baseline, method, **option)
                right, b = request(candidate, method, **option)
                assert (left.status_code, left.content) == (right.status_code, right.content), 'Static HTTP parity failed'
                assert right.status_code == 200
                if option == {'page': 1, 'size': 10}:
                    old, new = command_summary(a), command_summary(b)
                    assert (old['SQL'], old['PG_text_reads'], old['Mongo']) == (110, 12, 355)
                    assert (new['SQL'], new['PG_text_reads'], new['Mongo']) == (110, 12, 67)
                    assert (old['current_fingerprints'], old['verification_bags'], old['scoring_bags']) == (162, 108, 54)
                    assert (new['current_fingerprints'], new['verification_bags'], new['scoring_bags']) == (18, 12, 6)
                    report['counts'].append({'method': method, 'T1': old, 'T2': new})
                report['static_pairs'] += 1
            save()
        cases = [('initial', v) for v in ('bag', 'delete', 'unit')] + [
            ('score', v) for v in ('bag', 'delete', 'unit')] + [
            ('final', v) for v in ('bag', 'delete', 'inactive', 'binding', 'model', 'vector',
                                  'unit', 'two_versions', 'revoked', 'rejected', 'index', 'global', 'pg_text', 'pg_mongo')]
        cases += [(phase, case) for phase in ('initial', 'final')
                  for case in ('legacy', 'acl', 'missing_required', 'wrong_type')]
        for method in ('bow', 'vector', 'hybrid'):
            for phase, case in cases:
                results, bodies = [], []
                for service in (baseline, candidate):
                    entered, release, triggered = Event(), Event(), [False]
                    def barrier(entered=entered, release=release, triggered=triggered):
                        if not triggered[0]:
                            triggered[0] = True
                            entered.set()
                            assert release.wait(25)
                    def noop():
                        pass
                    restore = noop
                    with monkeypatch.context() as patch:
                        if phase == 'final':
                            original = service._metadata
                            calls = [0]
                            def metadata(*args, original=original, calls=calls, barrier=barrier, **kwargs):
                                calls[0] += 1
                                if calls[0] == 2:
                                    barrier()
                                return original(*args, **kwargs)
                            patch.setattr(service, '_metadata', metadata)
                        elif phase == 'initial':
                            original = c.indexing._verified
                            def verified(snapshot, *args, original=original, barrier=barrier, **kwargs):
                                if snapshot[0]['id'] == first['id']:
                                    barrier()
                                return original(snapshot, *args, **kwargs)
                            patch.setattr(c.indexing, '_verified', verified)
                        else:
                            name = 'get_bag' if service is baseline else 'search_bags'
                            original = getattr(c.indexing.bags, name)
                            def scoring(*args, original=original, barrier=barrier, **kwargs):
                                trace = ACTIVE.get()
                                if trace is not None and 'verified' not in trace.stack:
                                    barrier()
                                return original(*args, **kwargs)
                            patch.setattr(c.indexing.bags, name, scoring)
                        try:
                            with ThreadPoolExecutor(max_workers=1) as pool:
                                pending = pool.submit(request, service, method)
                                try:
                                    assert entered.wait(25), 'Expected observation boundary not reached'
                                    restore = mutate(case)
                                finally:
                                    release.set()
                                response, _ = pending.result(timeout=40)
                            body = response.json()
                            if response.status_code == 200:
                                assert all(item['resource_id'] != first['resource_id'] for item in body['items'])
                                assert body['total'] == (4 if case == 'two_versions' else 5)
                            else:
                                assert response.status_code == 409 and body['detail']['code'] == 'SEARCH_INDEX_STALE'
                            results.append(response.status_code)
                            bodies.append(response.content)
                        finally:
                            release.set()
                            restore()
                assert results[0] == results[1] and bodies[0] == bodies[1], (method, phase, case, results)
                report['races'].append({'method': method, 'phase': phase, 'case': case, 'statuses': results})
                save()
            print('T2_PHASE_RACES_PASS ' + method, flush=True)

        # Maps are request-local. Real concurrent BoW must not contaminate responses;
        # an independent writer was already exercised by every event race above.
        c.search = candidate
        reference, _ = request(candidate, 'bow')
        def concurrent(index):
            identifier = 't2-concurrent-' + str(index)
            response = api.post('/api/v1/search', json={'query': 'Page', 'method': 'bow'},
                headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
            row = probe.rows.pop(identifier)
            return response, row
        with ThreadPoolExecutor(max_workers=5) as pool:
            responses = list(pool.map(concurrent, range(5)))
        report['concurrency'] = [
            {'status': response.status_code, **command_summary(row),
             'mongo_operations': [op for op in row['operations'] if op['backend'] == 'mongo']}
            for response, row in responses]
        save()
        for response, row in responses:
            serial += 1
            failures.observe(response, method='bow', request_id=serial, duration_ms=row['total_ms'], fixture='medium')
            assert response.status_code == 200 and response.content == reference.content
            assert row['PG_exec_count'] == 110 and row['Mongo_cmd_count'] == 67
    report['status'] = 'TARGETED_PASS'
    save()

def source_hashes():
    import hashlib
    return {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
        'src/data_sync_etl/adapters/mongo/foundation_store.py', 'src/data_sync_etl/application/indexing.py',
        'src/data_sync_etl/application/search.py', 'src/data_sync_etl/ports/search.py')}


def test_t2_concurrency(t2_fixture, monkeypatch):
    from collections import Counter

    c, _first, _db, _versions, _baseline, candidate = t2_fixture
    probe = c._t1_probe
    observed, errors, collisions = {}, [], []
    original_started, original_succeeded = probe.mongo.started, probe.mongo.succeeded
    def started(event):
        trace = ACTIVE.get()
        if trace is not None:
            observed.setdefault(trace.identifier, Counter())['started'] += 1
            if probe.mongo.event_key(event) in probe.mongo.pending:
                collisions.append(trace.identifier)
        try:
            return original_started(event)
        except Exception as exc:
            errors.append({'callback': 'started', 'type': type(exc).__name__})
            raise
    def succeeded(event):
        trace = ACTIVE.get()
        if trace is not None:
            observed.setdefault(trace.identifier, Counter())['succeeded'] += 1
        try:
            return original_succeeded(event)
        except Exception as exc:
            errors.append({'callback': 'succeeded', 'type': type(exc).__name__})
            raise
    monkeypatch.setattr(probe.mongo, 'started', started)
    monkeypatch.setattr(probe.mongo, 'succeeded', succeeded)
    target = Path(os.environ['ETL_T1_OUTPUT'])
    report = {'status': 'RUNNING', 'groups': [], 'errors': errors, 'collisions': collisions}
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    c.search = candidate
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        def request(identifier):
            response = api.post('/api/v1/search', json={'query': 'Page', 'method': 'bow'},
                headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
            return response, probe.rows.pop(identifier)
        reference, _ = request('reference')
        for workers in (1, 2, 5, 10):
            with ThreadPoolExecutor(max_workers=workers) as pool:
                responses = list(pool.map(request, [f't2-{workers}-{i}' for i in range(workers)]))
            report['groups'].append({'workers': workers, 'rows': [
                {'status': r.status_code, **command_summary(row), **observed[row['span_id']]}
                for r, row in responses]})
            target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            assert all(r.status_code == 200 and r.content == reference.content for r, _ in responses)
            assert not errors and not collisions, 'Profiler callback failed or collided; counts invalid'
            assert all(row['PG_exec_count'] == 110 and row['Mongo_cmd_count'] == 67 for _, row in responses)
    report['status'] = 'CONCURRENCY_PASS'
    target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


def test_t2_benchmark(t2_fixture, monkeypatch):
    from collections import Counter
    from time import perf_counter, process_time

    from sqlalchemy import event

    from tests.integration.test_http_query_cache_pg import memory
    from tests.search_read_path_probe import summary
    from tests.search_t1_support import active_host_seconds

    c, first, db, versions, baseline, candidate = t2_fixture
    assert c.settings.search_timeout_seconds == 15
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    report = {'status': 'RUNNING', 'T1': T1_SHA, 'source_hashes': source_hashes(),
        'cache': 'OFF', 'model_dimension': 384, 'samples': [], 'groups': [], 'pairs': 0}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    light = {}
    def before(_c, _u, statement, _p, ctx, _m):
        trace = ACTIVE.get()
        if trace is not None:
            assert statement.lstrip().upper().startswith(('SELECT', 'WITH'))
            ctx.t2_started = perf_counter()
            ctx.t2_text = 'FROM content_unit_texts' in statement
    def after(_c, _u, _s, _p, ctx, _m):
        trace = ACTIVE.get()
        if trace is not None:
            row = light.setdefault(trace.identifier, Counter())
            row['SQL'] += 1
            row['PG_driver_ms'] += (perf_counter() - ctx.t2_started) * 1000
            if ctx.t2_text:
                row['PG_text_reads'] += 1
                row['PG_text_ms'] += (perf_counter() - ctx.t2_started) * 1000
    original_started, original_succeeded = probe.mongo.started, probe.mongo.succeeded
    def mongo_started(event):
        trace = ACTIVE.get()
        if trace is not None:
            light.setdefault(trace.identifier, Counter())['Mongo'] += 1
        return original_started(event)
    def mongo_succeeded(event):
        trace = ACTIVE.get()
        if trace is not None:
            light.setdefault(trace.identifier, Counter())['Mongo_driver_ms'] += event.duration_micros / 1000
        return original_succeeded(event)
    monkeypatch.setattr(probe.mongo, 'started', mongo_started)
    monkeypatch.setattr(probe.mongo, 'succeeded', mongo_succeeded)
    event.listen(c.engine, 'before_cursor_execute', before)
    event.listen(c.engine, 'after_cursor_execute', after)
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    serial = 0
    try:
        with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
            for profile in (False, True):
                for method in ('bow', 'vector', 'hybrid'):
                    for pair in range(30):
                        responses = []
                        for path, service in ((('T1', baseline), ('T2', candidate)) if pair % 2 == 0 else
                                              (('T2', candidate), ('T1', baseline))):
                            c.search = service
                            serial += 1
                            identifier = 't2-benchmark-' + str(serial)
                            host_start, cpu, started = active_host_seconds(), process_time(), perf_counter()
                            response = api.post('/api/v1/search',
                                json={'query': 'Page ' + ('fractions addition' if pair % 2 else 'text learning') + ' ' + str(pair),
                                      'method': method},
                                headers={'x-readpath-case': identifier, 'x-readpath-profile': str(int(profile))})
                            wall, cpu_ms = (perf_counter() - started) * 1000, (process_time() - cpu) * 1000
                            host_end = active_host_seconds()
                            host_ms = ((host_end - host_start) * 1000 if host_start is not None else None)
                            row = probe.rows.pop(identifier)
                            measured = light.pop(identifier)
                            sample = {'path': path, 'method': method, 'pair': pair, 'profile': profile,
                                'status': response.status_code, 'wall_ms': wall, 'asgi_ms': row['total_ms'],
                                'cpu_ms': cpu_ms, 'host_active_ms': host_ms, **memory(), **measured,
                                'model_prepare_ms': row['model_prepare_ms'] if profile else None,
                                'query_encode_ms': row['embed_ms'] if profile else None,
                                'score_rank_ms': row['score_rank_ms'] if profile else None}
                            if profile:
                                sample['hotspots'] = command_summary(row)
                            report['samples'].append(sample)
                            if response.status_code >= 500:
                                report['status'] = 'UNEXPECTED_5XX'
                                report['failed_phases'] = row['phases_inclusive_ms']
                            save()
                            failures.observe(response, method=method, request_id=serial, duration_ms=wall, fixture='medium')
                            assert response.status_code == 200 and response.json()['total'] == 6
                            assert measured['SQL'] == 110 and measured['PG_text_reads'] == 12
                            assert measured['Mongo'] == (355 if path == 'T1' else 67)
                            if profile:
                                counters = sample['hotspots']
                                assert (counters['current_fingerprints'], counters['verification_bags'], counters['scoring_bags']) == (
                                    (162, 108, 54) if path == 'T1' else (18, 12, 6))
                            if host_ms is not None:
                                assert wall - host_ms < 1000, 'Host suspension: evidence retained, comparison invalid'
                            responses.append(response.content)
                        assert responses[0] == responses[1], 'HTTP bytes/score/rank/page parity failed'
                        report['pairs'] += 1
                        save()
                    print(f'T2_BENCHMARK_30_PAIRS {method} profile={int(profile)}', flush=True)
        for profile in (False, True):
            for method in ('bow', 'vector', 'hybrid'):
                for path in ('T1', 'T2'):
                    rows = [r for r in report['samples'] if (r['profile'], r['method'], r['path']) == (profile, method, path)]
                    report['groups'].append({'profile': profile, 'method': method, 'path': path,
                        **{key: summary([r[key] for r in rows]) for key in (
                            'wall_ms', 'asgi_ms', 'PG_text_ms', 'PG_driver_ms', 'Mongo_driver_ms', 'cpu_ms', 'rss_bytes')},
                        'statuses': dict(Counter(str(r['status']) for r in rows))})
        report['status'] = 'BENCHMARK_PASS'
        save()
    finally:
        event.remove(c.engine, 'before_cursor_execute', before)
        event.remove(c.engine, 'after_cursor_execute', after)
