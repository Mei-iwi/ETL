"""Explicit physical T1 recovery gates; never targets Dev or logs result payloads."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from threading import Event

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event

from data_sync_etl.db.models import (
    ContentUnit,
    ContentUnitText,
    MasterLearningResource,
    ResourceVersion,
)
from data_sync_etl.main import create_app
from tests.conftest import make_pdf
from tests.integration.test_search_t1_physical import (
    BaselineSearch,
    CandidateSearch,
)
from tests.integration.test_search_t1_physical import (
    container as container,
)
from tests.integration.test_search_t1_physical import (
    physical_search as physical_search,
)
from tests.search_read_path_probe import TraceMiddleware
from tests.search_t1_support import FailureCheckpoint, active_host_seconds
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_T1_PHYSICAL') != '1', reason='Owned T1 runner required')]


def seed_medium(c, first, db):
    versions = [first]
    for index, pages in ((2, 3), (3, 12), (4, 12), (5, 12), (6, 12)):
        rid = f't1-recovery-{index}'
        with c.uow() as repo:
            original = repo.get('master_learning_resources', first['resource_id'])
            repo.insert('master_learning_resources', {**original, 'id': rid, 'mongo_id': None,
                        'title': 'Isolated T1 fixture', 'source_origin': 'LOCAL'})
        make_pdf(c, ['Page fractions addition learning ' + str(n) for n in range(pages)], name=f'recovery-{index}.pdf')
        version = c.ingestion.run(rid, 'input', f'recovery-{index}.pdf')['version']
        c.ocr.create_job(version['id'])
        while c.worker.once('owned-recovery-fixture'):
            pass
        for doc in c.documents.units.values():
            if doc['resource_version_id'] == version['id']:
                db.mongo_content_units.replace_one({'_id': doc['_id']}, doc, upsert=True)
        c.indexing.enqueue(version['id'], force=True)
        assert c.indexing.once()
        versions.append(version)
    return versions


def test_race_semantics_recovery(physical_search, monkeypatch):
    c, first, db = physical_search
    versions = seed_medium(c, first, db)
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    report = {'status': 'RUNNING', 'stable_pairs': 0, 'races': [], 'concurrency': [],
              'cache': 'OFF', 'source_hashes': {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
                for p in ('src/data_sync_etl/application/search.py', 'src/data_sync_etl/application/indexing.py',
                          'src/data_sync_etl/adapters/persistence/search_reader.py')}}
    def save():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    baseline = BaselineSearch(c.indexing, c.settings, c.indexing.bags)
    candidate = CandidateSearch(c.indexing, c.settings, c.indexing.bags)
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    serial = 0
    with c.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': first['id']}, order=('sequence_no', 'id'))
        second_units = repo.find('content_units', {'resource_version_id': versions[1]['id']})
        subject = repo.find('resource_subjects', {'resource_id': first['resource_id']})[0]['subject_id']
        grade = repo.find('resource_grade_levels', {'resource_id': first['resource_id']})[0]['grade_level_id']
        original_resource = repo.get('master_learning_resources', first['resource_id'])
        original_version = repo.get('resource_versions', first['id'])
        stage = c.indexing._runs(repo, first['id'])[0]
    bag_id = units[0]['id']

    def update(table, key, values):
        with c.uow() as repo:
            row = repo.get(table, key)
            old = {name: row[name] for name in values}
            repo.update(table, key, values)
        def undo():
            with c.uow() as repo:
                repo.update(table, key, old)
        return undo

    def mutate(case):
        undos = []
        if case in ('text', 'before_text', 'first_text', 'two_versions', 'both_databases'):
            undos.append(update('content_unit_texts', units[-1]['id'], {'text': 'Changed in owned PG fixture'}))
        if case == 'two_versions':
            undos.append(update('content_unit_texts', second_units[-1]['id'], {'text': 'Changed second owned version'}))
        if case == 'revoked':
            undos.append(update('master_learning_resources', first['resource_id'], {'publication_status': 'DRAFT'}))
        if case == 'permission':
            undos.append(update('content_units', units[0]['id'], {'review_status': 'REJECTED'}))
        if case == 'index':
            undos.append(update('etl_stage_runs', stage['id'], {'status': 'PENDING'}))
        if case == 'delete_unit':
            with c.uow() as repo:
                old_body = repo.get('content_unit_texts', units[-1]['id'])
                old_unit = repo.get('content_units', units[-1]['id'])
                repo.session.execute(delete(ContentUnitText).where(ContentUnitText.content_unit_id == old_unit['id']))
                repo.session.execute(delete(ContentUnit).where(ContentUnit.id == old_unit['id']))
            def restore_unit():
                with c.uow() as repo:
                    repo.insert('content_units', old_unit)
                    repo.insert('content_unit_texts', old_body)
            undos.append(restore_unit)
        if case == 'add_unit':
            with c.uow() as repo:
                source_body = repo.get('content_unit_texts', units[0]['id'])
                repo.insert('content_units', {**units[0], 'id': 't1-added-unit', 'mongo_id': 't1-added-unit', 'sequence_no': 999})
                repo.insert('content_unit_texts', {**source_body, 'content_unit_id': 't1-added-unit'})
            def remove_unit():
                with c.uow() as repo:
                    repo.session.execute(delete(ContentUnitText).where(ContentUnitText.content_unit_id == 't1-added-unit'))
                    repo.session.execute(delete(ContentUnit).where(ContentUnit.id == 't1-added-unit'))
            undos.append(remove_unit)
        if case == 'corpus':
            with c.uow() as repo:
                repo.insert('master_learning_resources', {**original_resource, 'id': 't1-new-resource', 'mongo_id': None, 'source_origin': 'LOCAL'})
                repo.insert('resource_versions', {**original_version, 'id': 't1-new-version', 'resource_id': 't1-new-resource', 'mongo_id': None})
            def remove_resource():
                with c.uow() as repo:
                    repo.session.execute(delete(ResourceVersion).where(ResourceVersion.id == 't1-new-version'))
                    repo.session.execute(delete(MasterLearningResource).where(MasterLearningResource.id == 't1-new-resource'))
            undos.append(remove_resource)
        if case in ('bag', 'embedding', 'mongo_unit', 'both_databases'):
            collection = db.content_bags if case in ('bag', 'embedding') else db.mongo_content_units
            key = bag_id if case in ('bag', 'embedding') else units[-1]['id']
            old = collection.find_one({'_id': key})
            changes = ({'keywords': ['changed'], 'embedding_text': 'changed'} if case == 'bag' else
                       {'embedding': [-value for value in old['embedding']]} if case == 'embedding' else
                       {'text': 'Changed in owned PG fixture' if case == 'both_databases' else 'Mongo-only mutation'})
            collection.update_one({'_id': key}, {'$set': changes})
            old = deepcopy(old)
            undos.append(lambda collection=collection, key=key, old=old: collection.replace_one({'_id': key}, old))
        def undo_all():
            for undo in reversed(undos):
                undo()
        return undo_all

    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        def request(service, method, **options):
            nonlocal serial
            serial += 1
            c.search = service
            identifier = 'recovery-' + str(serial)
            response = api.post('/api/v1/search', json={'query': 'Page', 'method': method, **options},
                headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
            row = probe.rows.pop(identifier)
            failures.observe(response, method=method, request_id=serial, duration_ms=row['total_ms'], fixture='medium')
            return response, row
        # Static parity across the real HTTP response, paging and all filters.
        for method in ('bow', 'vector', 'hybrid'):
            options = [{'page': page, 'size': size} for page in (1, 2, 1, 101) for size in (1, 10, 50)]
            options += [{'filters': {key: value}} for key, value in (
                ('subject_id', subject), ('grade_level_id', grade), ('resource_id', first['resource_id']),
                ('resource_version_id', first['id']), ('resource_type_code', original_resource['resource_type_code']),
                ('resource_id', 'missing'))]
            for option in options:
                before, _ = request(baseline, method, **option)
                after, row = request(candidate, method, **option)
                equal = (before.status_code, before.content) == (after.status_code, after.content)
                assert equal, 'Static response bytes differ (payload suppressed)'
                assert after.status_code == 200
                if option == {'page': 1, 'size': 10}:
                    assert row['PG_exec_count'] == 110 and row['Mongo_cmd_count'] == 355
                report['stable_pairs'] += 1
            save()
        # Same observation points: before initial text retrieval or before final metadata.
        # first_text deliberately retains the OLD non-equivalent point-vs-batch schedule.
        cases = ('before_text', 'first_text', 'text', 'two_versions', 'bag', 'embedding',
                 'delete_unit', 'add_unit', 'revoked', 'permission', 'index', 'corpus',
                 'mongo_unit', 'both_databases')
        for method in ('bow', 'vector', 'hybrid'):
            for case in cases:
                recorded = []
                for service in (baseline, candidate):
                    entered, release = Event(), Event()
                    triggered = [False]
                    def barrier(entered=entered, release=release, triggered=triggered):
                        if not triggered[0]:
                            triggered[0] = True
                            entered.set()
                            assert release.wait(20), 'Writer release timeout'
                    def undo():
                        pass
                    hook = None
                    with monkeypatch.context() as patch:
                        if case == 'before_text':
                            original = c.indexing.reader.units
                            def before_text(version_id, *args, original=original, barrier=barrier, **kwargs):
                                result = original(version_id, *args, **kwargs)
                                if version_id == first['id']:
                                    barrier()
                                return result
                            patch.setattr(c.indexing.reader, 'units', before_text)
                        elif case == 'first_text':
                            def hook(_c, _u, statement, _p, _x, _m, barrier=barrier):
                                if statement.lstrip().upper().startswith('SELECT') and 'FROM content_unit_texts' in statement:
                                    barrier()
                            event.listen(c.engine, 'after_cursor_execute', hook)
                        else:
                            original = service._metadata
                            calls = [0]
                            def before_final(*args, original=original, calls=calls, barrier=barrier, **kwargs):
                                calls[0] += 1
                                if calls[0] == 2:
                                    barrier()
                                return original(*args, **kwargs)
                            patch.setattr(service, '_metadata', before_final)
                        try:
                            with ThreadPoolExecutor(max_workers=1) as pool:
                                pending = pool.submit(request, service, method)
                                try:
                                    assert entered.wait(25), 'Expected gate not reached'
                                    undo = mutate(case)
                                finally:
                                    release.set()
                                response, row = pending.result(timeout=35)
                            # Reference starts after mutation, with no race wrapper. A fresh
                            # baseline proves the exact survivor ranking/scores/snippets/total.
                            if case not in ('bag', 'embedding', 'index', 'corpus', 'mongo_unit') and response.status_code == 200:
                                reference, _ = request(BaselineSearch(c.indexing, c.settings, c.indexing.bags), method)
                                equal = response.content == reference.content
                                assert equal, 'Survivor response differs from fresh baseline'
                            if service is candidate:
                                expected = 409 if case in ('bag', 'embedding', 'index', 'corpus', 'mongo_unit') else 200
                                assert response.status_code == expected, (case, response.status_code)
                                if expected == 200:
                                    assert response.json()['total'] == (4 if case == 'two_versions' else 5)
                                    assert all(item['resource_id'] != first['resource_id'] for item in response.json()['items'])
                            recorded.append({'path': 'baseline' if service is baseline else 'batch',
                                'status': response.status_code, 'total': response.json().get('total'),
                                'code': response.json().get('detail', {}).get('code'), 'hash': row['response_hash']})
                        finally:
                            release.set()
                            if hook is not None:
                                event.remove(c.engine, 'after_cursor_execute', hook)
                            undo()
                report['races'].append({'method': method, 'case': case,
                    'observation': 'non-equivalent row read instants' if case == 'first_text' else 'equivalent boundary',
                    'results': recorded})
                save()
            print('T1_RECOVERY_RACES_PASS ' + method, flush=True)
    report['status'] = 'RACES_PASS_REMAINING_GATES_PENDING'
    save()

def test_concurrency_and_access_recovery(physical_search, monkeypatch):
    """Actual HTTP admission + live PG/Mongo; synchronization is event-driven."""
    from queue import Queue
    from threading import Barrier

    from data_sync_etl.domain.search_access import SearchAccess
    from tests.indexing_support import seed_structure

    c, first, db = physical_search
    seed_medium(c, first, db)
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    if os.environ.get('ETL_T1_PHASE') != 'extra':
        target = target.with_name(target.stem + '_EXTRA.json')
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    report = {'status': 'RUNNING', 'concurrency': [], 'access_pairs': 0, 'large_structure_pairs': 0}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    c.settings.search_timeout_seconds = 120  # Capacity test, NOT latency benchmark.
    services = (BaselineSearch(c.indexing, c.settings, c.indexing.bags),
                CandidateSearch(c.indexing, c.settings, c.indexing.bags))
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    serial = 0
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        for method in ('bow', 'vector', 'hybrid'):
            for count in (1, 2, 5, 10):
                comparison = []
                for path, service in zip(('baseline', 'batch'), services, strict=True):
                    c.search = service
                    completed, start, entered, release = Queue(), Barrier(count + 1), Event(), Event()
                    prepare = c.indexing.embedding.prepare_query
                    def held_prepare(prepare=prepare, entered=entered, release=release):
                        encoder = prepare()
                        def encode(query):
                            entered.set()
                            assert release.wait(35), 'Admission release timeout'
                            return encoder(query)
                        return encode
                    prefix = f'capacity-{method}-{count}-{path}'
                    def call(index, prefix=prefix, start=start, completed=completed, method=method):
                        start.wait(timeout=15)
                        identifier = prefix + '-' + str(index)
                        response = api.post('/api/v1/search', json={'query': 'Page', 'method': method},
                            headers={'x-readpath-case': identifier, 'x-readpath-profile': '0'})
                        completed.put((response, probe.rows.pop(identifier)))
                    with monkeypatch.context() as patch:
                        if method != 'bow':
                            patch.setattr(c.indexing.embedding, 'prepare_query', held_prepare)
                        with ThreadPoolExecutor(max_workers=count) as pool:
                            pending = [pool.submit(call, i) for i in range(count)]
                            try:
                                start.wait(timeout=15)
                                if method != 'bow':
                                    assert entered.wait(20), 'Encoder not reached'
                                    early = [completed.get(timeout=20) for _ in range(count - 1)]
                                    assert all(response.status_code == 503 and
                                        response.json()['detail']['code'] == 'SEARCH_RUNTIME_BUSY'
                                        for response, _ in early)
                                    release.set()
                                    rows = early + [completed.get(timeout=35)]
                                else:
                                    rows = [completed.get(timeout=90) for _ in range(count)]
                            finally:
                                release.set()
                            for future in pending:
                                future.result(timeout=40)
                    for response, row in rows:
                        serial += 1
                        failures.observe(response, method=method, request_id=serial,
                                         duration_ms=row['total_ms'], fixture='medium')
                    good = [response for response, _ in rows if response.status_code == 200]
                    assert len(good) == (count if method == 'bow' else 1)
                    assert all(response.content == good[0].content for response in good)
                    comparison.append((len(good), count - len(good), good[0].content))
                    report['concurrency'].append({'method': method, 'concurrency': count,
                        'path': path, '200': len(good), '503_busy': count - len(good)})
                assert comparison[0] == comparison[1], 'Concurrency parity failed'
                save()

        # Public never reads the live corpus, even when candidate batching is opted in.
        for method in ('bow', 'vector', 'hybrid'):
            before = services[0].search('Page', method, access=SearchAccess())
            after = services[1].search('Page', method, access=SearchAccess())
            assert before == after and not after['items'] and after['total'] == 0
            report['access_pairs'] += 1

        # >100 units, empty structural parents, Mongo pagination, stable order and text.
        rid = 't1-large-structured'
        with c.uow() as repo:
            original = repo.get('master_learning_resources', first['resource_id'])
            repo.insert('master_learning_resources', {**original, 'id': rid, 'mongo_id': None,
                'title': 'Isolated hierarchy', 'source_origin': 'LOCAL'})
        original_postprocess = c.postprocess.run_locked
        def hierarchy(version_id, **kwargs):
            result = original_postprocess(version_id, **kwargs)
            seed_structure(c, version_id)
            with c.uow() as repo:
                for unit in repo.find('content_units', {'resource_version_id': version_id}, limit=1000):
                    repo.update('content_units', unit['id'], {'review_status': 'APPROVED', 'retrieval_eligible': True})
            return result
        with monkeypatch.context() as patch:
            patch.setattr(c.postprocess, 'run_locked', hierarchy)
            make_pdf(c, ['Page fractions ' + str(i) for i in range(101)], name='t1-structure.pdf')
            version = c.ingestion.run(rid, 'input', 't1-structure.pdf')['version']
            c.ocr.create_job(version['id'])
            while c.worker.once('owned-t1-structure'):
                pass
        for doc in c.documents.units.values():
            if doc['resource_version_id'] == version['id']:
                db.mongo_content_units.replace_one({'_id': doc['_id']}, doc, upsert=True)
        c.indexing.enqueue(version['id'], force=True)
        assert c.indexing.once()
        with c.uow() as repo:
            initial = c.indexing._snapshot(repo, version['id'])
            batched = c.indexing._snapshot(repo, version['id'], text_reader=repo.search_unit_texts)
            assert initial == batched and len(initial[2]) == 104
        for restricted in (False, True):
            if restricted:
                with c.uow() as repo:
                    root = next(unit for unit in initial[2] if unit['parent_unit_id'] is None)
                    repo.update('content_units', root['id'], {'retrieval_eligible': False})
                # Index fingerprint is now stale; neither path may expose this version.
            for method in ('bow', 'vector', 'hybrid'):
                responses = []
                for _path, service in zip(('baseline', 'batch'), services, strict=True):
                    c.search = service
                    serial += 1
                    identifier = 'hierarchy-' + str(serial)
                    response = api.post('/api/v1/search', json={'query': 'Page', 'method': method,
                        'filters': {'resource_id': rid}},
                        headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
                    row = probe.rows.pop(identifier)
                    failures.observe(response, method=method, request_id=serial,
                                     duration_ms=row['total_ms'], fixture='medium')
                    assert response.status_code == 200
                    assert response.json()['total'] == (0 if restricted else 1)
                    responses.append(response.content)
                assert responses[0] == responses[1]
                report['large_structure_pairs'] += 1
        report['status'] = 'EXTRA_GATES_PASS'
        save()

def test_benchmark_recovery(physical_search):
    """Balanced paired timings. Full profiling and lean wall time are separate."""
    from collections import Counter
    from time import perf_counter, process_time

    from tests.integration.test_http_query_cache_pg import memory
    from tests.search_read_path_probe import ACTIVE, summary

    c, first, db = physical_search
    seed_medium(c, first, db)
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    report = {'status': 'RUNNING', 'cache': 'OFF', 'model_dimension': 384,
        'source_hashes': {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in ('src/data_sync_etl/application/search.py', 'src/data_sync_etl/application/indexing.py',
                      'src/data_sync_etl/adapters/persistence/search_reader.py')},
        'samples': [], 'groups': [], 'response_parity_pairs': 0, 'data_changes': 'NONE'}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    active = {}
    def before(_c, _u, statement, _p, ctx, _m):
        trace = ACTIVE.get()
        if trace is not None:
            ctx.t1_text_started = perf_counter() if 'FROM content_unit_texts' in statement else None
    def after(_c, _u, _s, _p, ctx, _m):
        started = getattr(ctx, 't1_text_started', None)
        trace = ACTIVE.get()
        if trace is not None and started is not None:
            sample = active.setdefault(trace.identifier, {'text_reads': 0, 'text_read_ms': 0.})
            sample['text_reads'] += 1
            sample['text_read_ms'] += (perf_counter() - started) * 1000
    event.listen(c.engine, 'before_cursor_execute', before)
    event.listen(c.engine, 'after_cursor_execute', after)
    services = {'baseline': BaselineSearch(c.indexing, c.settings, c.indexing.bags),
                'batch': CandidateSearch(c.indexing, c.settings, c.indexing.bags)}
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    serial = 0
    try:
        with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
            for profile in (False, True):
                for method in ('bow', 'vector', 'hybrid'):
                    for pair in range(30):
                        responses = []
                        # Identical normalized query within each pair, 30 workloads;
                        # Page ensures all six resources are candidates for ranking.
                        query = 'Page ' + ('fractions addition learning' if pair % 2 else 'text learning') + ' ' + str(pair)
                        for path in (('baseline', 'batch') if pair % 2 == 0 else ('batch', 'baseline')):
                            c.search = services[path]
                            serial += 1
                            identifier = 'benchmark-' + str(serial)
                            rss_before = memory()
                            host_started = active_host_seconds()
                            cpu = process_time()
                            started = perf_counter()
                            response = api.post('/api/v1/search', json={'query': query, 'method': method},
                                headers={'x-readpath-case': identifier, 'x-readpath-profile': str(int(profile))})
                            elapsed, cpu_ms = (perf_counter() - started) * 1000, (process_time() - cpu) * 1000
                            host_finished = active_host_seconds()
                            host_ms = ((host_finished - host_started) * 1000
                                       if host_started is not None and host_finished is not None else None)
                            row = probe.rows.pop(identifier)
                            text = active.pop(identifier, {'text_reads': 0, 'text_read_ms': 0.})
                            if response.status_code >= 500:
                                # Persist stage/candidate identity BEFORE the fail-closed
                                # checkpoint raises; never lose a long-running failure.
                                report['status'] = 'BLOCKED_UNEXPECTED_5XX'
                                report['failed_request'] = {'request_id': serial, 'path': path,
                                    'method': method, 'profile': profile, 'pair': pair,
                                    'wall_ms': elapsed, 'cpu_ms': cpu_ms, **text,
                                    'host_active_ms': host_ms,
                                    'phases_inclusive_ms': row['phases_inclusive_ms'],
                                    'PG_exec_ms': row['PG_exec_ms'], 'Mongo_cmd_ms': row['Mongo_cmd_ms'],
                                    'PG_exec_count': row['PG_exec_count'] if profile else None,
                                    'Mongo_cmd_count': row['Mongo_cmd_count'] if profile else None}
                                save()
                            failures.observe(response, method=method, request_id=serial,
                                             duration_ms=elapsed, fixture='medium')
                            assert response.status_code == 200
                            assert response.json()['total'] == 6
                            assert text['text_reads'] == (108 if path == 'baseline' else 12)
                            if profile:
                                assert row['PG_exec_count'] == (206 if path == 'baseline' else 110)
                                assert row['Mongo_cmd_count'] == 355
                            responses.append(response.content)
                            report['samples'].append({'method': method, 'path': path, 'profile': profile,
                                'pair': pair, 'status': response.status_code, 'wall_ms': elapsed, 'cpu_ms': cpu_ms,
                                'asgi_ms': row['total_ms'], 'host_active_ms': host_ms,
                                **text, **memory(), 'rss_before_bytes': rss_before['rss_bytes'],
                                'PG_exec_count': row['PG_exec_count'] if profile else None,
                                'Mongo_cmd_count': row['Mongo_cmd_count'] if profile else None,
                                'model_prepare_ms': row['model_prepare_ms'] if profile else None,
                                'query_encode_ms': row['embed_ms'] if profile else None,
                                'score_rank_ms': row['score_rank_ms'] if profile else None})
                            save()  # Preserve each completed request, not just completed pairs.
                            if host_ms is not None:
                                assert elapsed - host_ms < 1000, 'Host suspended: samples retained, comparison invalid'
                        assert responses[0] == responses[1], 'Paired HTTP body parity failed'
                        report['response_parity_pairs'] += 1
                        save()
                    print(f'T1_BENCHMARK_30_PAIRS {method} profile={int(profile)}', flush=True)
        for profile in (False, True):
            for method in ('bow', 'vector', 'hybrid'):
                for path in ('baseline', 'batch'):
                    rows = [r for r in report['samples'] if (r['profile'], r['method'], r['path']) == (profile, method, path)]
                    report['groups'].append({'method': method, 'path': path, 'profile': profile,
                        'wall_ms': summary([r['wall_ms'] for r in rows]),
                        'text_read_ms': summary([r['text_read_ms'] for r in rows]),
                        'cpu_ms': summary([r['cpu_ms'] for r in rows]),
                        'rss_bytes': summary([r['rss_bytes'] for r in rows]),
                        'statuses': dict(Counter(str(r['status']) for r in rows))})
        report['status'] = 'MEASURED_NOT_AUTOMATIC_ACCEPTANCE'
        save()
    finally:
        event.remove(c.engine, 'before_cursor_execute', before)
        event.remove(c.engine, 'after_cursor_execute', after)

def test_postgres_invariants_recovery(physical_search):
    from sqlalchemy.exc import IntegrityError

    from data_sync_etl.domain.indexing import IndexingError

    c, first, db = physical_search
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    report = {'status': 'RUNNING', 'pairs': 0, 'constraints': [], 'cache': 'OFF'}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    with c.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': first['id']}, order=('sequence_no', 'id'))
        old_body = repo.get('content_unit_texts', units[0]['id'])
        original = repo.get('resource_versions', first['id'])
    with pytest.raises(IntegrityError), c.uow() as repo:
        repo.update('content_unit_texts', units[0]['id'], {'text': None})
    report['constraints'].append('PG NULL rejected')
    with pytest.raises(IntegrityError), c.uow() as repo:
        repo.insert('content_unit_texts', old_body)
    report['constraints'].append('PG duplicate PK rejected')
    sizes = []
    def count(_c, _u, statement, params, _ctx, _many):
        if 'FROM content_unit_texts' in statement:
            sizes.append(len(params))
    event.listen(c.engine, 'before_cursor_execute', count)
    try:
        with c.uow() as repo:
            assert repo.search_unit_texts([f'absent-{i}' for i in range(1001)]) == {}
            assert sizes == [500, 500, 1]
            with pytest.raises(IndexingError):
                repo.search_unit_texts([units[0]['id'], units[0]['id']])
    finally:
        event.remove(c.engine, 'before_cursor_execute', count)
    report['constraints'].append('PG 1001 ID bounded 500/500/1; duplicate input rejected')
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    serial = 0
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        def parity(expected, filters=None):
            nonlocal serial
            for method in ('bow', 'vector', 'hybrid'):
                responses = []
                for cls in (BaselineSearch, CandidateSearch):
                    c.search = cls(c.indexing, c.settings, c.indexing.bags)
                    serial += 1
                    identifier = 'invariant-' + str(serial)
                    response = api.post('/api/v1/search', json={'query': 'Page', 'method': method,
                        'filters': filters or {}},
                        headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
                    probe.rows.pop(identifier)
                    assert response.status_code == 200 and response.json()['total'] == expected
                    responses.append(response.content)
                assert responses[0] == responses[1]
                report['pairs'] += 1
        # Initial missing row: neither point nor batch can use its previous Mongo text.
        with c.uow() as repo:
            repo.session.execute(delete(ContentUnitText).where(ContentUnitText.content_unit_id == units[0]['id']))
        parity(0)
        with c.uow() as repo:
            repo.insert('content_unit_texts', old_body)
        parity(1)
        # Explicit source empty text and Mongo mismatch: fail closed, no fake normalization.
        with c.uow() as repo:
            repo.update('content_unit_texts', units[0]['id'], {'text': ''})
        parity(0)
        with c.uow() as repo:
            repo.update('content_unit_texts', units[0]['id'], {'text': old_body['text']})
        # Newer eligible but unprocessed version hides older default, explicit old filter works.
        with c.uow() as repo:
            repo.insert('resource_versions', {**original, 'id': 't1-second-version', 'mongo_id': None,
                'version_number': 2, 'parent_version_id': first['id']})
        parity(0)
        parity(1, {'resource_version_id': first['id']})
        parity(0, {'resource_version_id': 't1-second-version'})
        # Limited access on a still READY fingerprint; denied leaf cannot supply score/snippet.
        with c.uow() as repo:
            repo.update('content_units', units[0]['id'], {'review_status': 'REJECTED'})
        c.indexing.enqueue(first['id'], force=True)
        assert c.indexing.once()
        parity(1, {'resource_version_id': first['id']})
        row = db.mongo_content_units.find_one({'_id': units[0]['id']})
        assert row is not None
    report['status'] = 'INVARIANTS_PASS'
    save()

def test_timeout_diagnosis(physical_search, monkeypatch):
    """Two bounded, instrumented attempts; real deadlines stay unchanged."""
    from time import monotonic, perf_counter, process_time

    from tests.search_read_path_probe import ACTIVE
    from tests.search_t1_support import UnexpectedSearch5xx

    c, first, db = physical_search
    seed_medium(c, first, db)
    assert c.settings.search_timeout_seconds == 15
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    report = {'status': 'RUNNING', 'cache': 'OFF', 'deadline_seconds': 15,
        'awake_guard': 'thread-scoped SYSTEM_REQUIRED, previous state restored on runner exit',
        'samples': [], 'source_hashes': {str(p): hashlib.sha256(Path(p).read_bytes()).hexdigest()
            for p in ('src/data_sync_etl/application/search.py', 'src/data_sync_etl/application/indexing.py',
                      'src/data_sync_etl/adapters/persistence/search_reader.py')}}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    original = BaselineSearch._deadline
    deadlines = {}
    def deadline(service, started):
        trace = ACTIVE.get()
        if trace is not None:
            deadlines[trace.identifier] = {
                'last_phase': trace.stack[-1] if trace.stack else 'search',
                'elapsed_query_ms': (monotonic() - started) * 1000}
        return original(service, started)
    monkeypatch.setattr(BaselineSearch, '_deadline', deadline)
    monkeypatch.setattr(CandidateSearch, '_deadline', deadline)
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    services = {'baseline': BaselineSearch(c.indexing, c.settings, c.indexing.bags),
                'batch': CandidateSearch(c.indexing, c.settings, c.indexing.bags)}
    save()
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        for attempt in range(2):
            responses = []
            for path in ('baseline', 'batch'):
                for repeat in range(2):
                    c.search = services[path]
                    identifier = f'diagnosis-{attempt}-{path}-{repeat}'
                    host_start, started, cpu = active_host_seconds(), perf_counter(), process_time()
                    response = api.post('/api/v1/search', json={'query': 'Page fractions', 'method': 'hybrid'},
                        headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
                    wall, cpu_ms = (perf_counter() - started) * 1000, (process_time() - cpu) * 1000
                    host_end = active_host_seconds()
                    host_ms = ((host_end - host_start) * 1000
                               if host_start is not None and host_end is not None else None)
                    row = probe.rows.pop(identifier)
                    report['samples'].append({'attempt': attempt, 'path': path, 'request_id': identifier,
                        'status': response.status_code, 'wall_ms': wall, 'asgi_ms': row['total_ms'],
                        'cpu_ms': cpu_ms, 'host_active_ms': host_ms,
                        'PG_exec_count': row['PG_exec_count'], 'PG_exec_ms': row['PG_exec_ms'],
                        'Mongo_cmd_count': row['Mongo_cmd_count'], 'Mongo_cmd_ms': row['Mongo_cmd_ms'],
                        'phases_inclusive_ms': row['phases_inclusive_ms'],
                        'last_deadline': deadlines.pop(identifier, None)})
                    save()
                    try:
                        failures.observe(response, method='hybrid', request_id=len(report['samples']),
                            duration_ms=wall, fixture='medium')
                    except UnexpectedSearch5xx:
                        continue  # Exactly two bounded attempts, every failure remains in checkpoint.
                    assert response.status_code == 200
                    assert row['PG_exec_count'] == (206 if path == 'baseline' else 110)
                    assert row['Mongo_cmd_count'] == 355
                    if host_ms is not None:
                        assert wall - host_ms < 1000
                    responses.append(response.content)
            if len(responses) == 4:
                assert all(body == responses[0] for body in responses)
    report['status'] = 'DIAGNOSIS_PASS' if all(r['status'] == 200 for r in report['samples']) else 'DIAGNOSIS_FAILED'
    save()
    assert report['status'] == 'DIAGNOSIS_PASS', 'Two attempts finished; failure evidence retained'

def test_http_activation_verification(physical_search, monkeypatch):
    """Final one-round verification of REGULAR HTTP Search, not test-only opt-in."""
    from data_sync_etl.application.search import SearchService

    c, first, db = physical_search
    seed_medium(c, first, db)
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    report = {'status': 'RUNNING', 'cache': 'OFF', 'http_pairs': [], 'races': []}
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    actual = SearchService(c.indexing, c.settings, c.indexing.bags)
    baseline = BaselineSearch(c.indexing, c.settings, c.indexing.bags)
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    serial = 0
    with c.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': first['id']}, order=('sequence_no', 'id'))
        old_text = repo.get('content_unit_texts', units[-1]['id'])['text']
        original_resource = repo.get('master_learning_resources', first['resource_id'])
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        def request(service, method, **options):
            nonlocal serial
            c.search = service
            serial += 1
            identifier = 'activated-http-' + str(serial)
            response = api.post('/api/v1/search', json={'query': 'Page', 'method': method, **options},
                headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
            row = probe.rows.pop(identifier)
            failures.observe(response, method=method, request_id=serial,
                duration_ms=row['total_ms'], fixture='medium')
            return response, row
        for method in ('bow', 'vector', 'hybrid'):
            for options in ({'page': 1, 'size': 1}, {'page': 2, 'size': 1}, {'page': 1, 'size': 10},
                            {'filters': {'resource_id': 'absent-resource'}}):
                before, before_row = request(baseline, method, **options)
                after, row = request(actual, method, **options)
                assert (before.status_code, before.content) == (after.status_code, after.content)
                assert after.status_code == 200
                if options == {'page': 1, 'size': 10}:
                    assert before_row['PG_exec_count'] == 206 and row['PG_exec_count'] == 110
                    assert before_row['Mongo_cmd_count'] == row['Mongo_cmd_count'] == 355
                    assert sum(op['backend'] == 'pg' and op.get('tables') == ['content_unit_texts']
                               for op in row['operations']) == 12
                report['http_pairs'].append({'method': method, 'options': options,
                    'baseline_SQL': before_row['PG_exec_count'], 'HTTP_SQL': row['PG_exec_count'],
                    'baseline_Mongo': before_row['Mongo_cmd_count'], 'HTTP_Mongo': row['Mongo_cmd_count']})
                save()
            for case in ('text', 'revoked', 'bag'):
                entered, release = Event(), Event()
                calls = [0]
                original = actual._metadata
                def barrier(*args, calls=calls, original=original, entered=entered, release=release, **kwargs):
                    calls[0] += 1
                    if calls[0] == 2:
                        entered.set()
                        assert release.wait(20)
                    return original(*args, **kwargs)
                old_bag = None
                with monkeypatch.context() as patch:
                    patch.setattr(actual, '_metadata', barrier)
                    try:
                        with ThreadPoolExecutor(max_workers=1) as pool:
                            pending = pool.submit(request, actual, method)
                            try:
                                assert entered.wait(20), 'Final HTTP observation not reached'
                                if case == 'bag':
                                    old_bag = db.content_bags.find_one({'_id': units[0]['id']})
                                    db.content_bags.update_one({'_id': units[0]['id']},
                                        {'$set': {'embedding': [-v for v in old_bag['embedding']]}})
                                else:
                                    with c.uow() as repo:
                                        if case == 'text':
                                            repo.update('content_unit_texts', units[-1]['id'], {'text': 'Owned activated HTTP race'})
                                        else:
                                            repo.update('master_learning_resources', first['resource_id'],
                                                {'publication_status': 'DRAFT'})
                            finally:
                                release.set()
                            response, _ = pending.result(timeout=30)
                        if case == 'bag':
                            assert response.status_code == 409
                            assert response.json()['detail']['code'] == 'SEARCH_INDEX_STALE'
                        else:
                            reference, _ = request(baseline, method)
                            assert response.status_code == 200 and response.json()['total'] == 5
                            assert all(item['resource_id'] != first['resource_id'] for item in response.json()['items'])
                            assert response.content == reference.content
                        report['races'].append({'method': method, 'case': case, 'status': response.status_code})
                        save()
                    finally:
                        release.set()
                        if old_bag is not None:
                            db.content_bags.replace_one({'_id': old_bag['_id']}, old_bag)
                        with c.uow() as repo:
                            repo.update('content_unit_texts', units[-1]['id'], {'text': old_text})
                            repo.update('master_learning_resources', first['resource_id'],
                                {'publication_status': original_resource['publication_status']})
    report['status'] = 'HTTP_ACTIVATION_PASS'
    save()
