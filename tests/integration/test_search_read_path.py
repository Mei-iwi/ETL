"""Physical read-path audit: newly owned PG/Mongo only, real model, cache OFF."""

import hashlib
import json
import os
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.application.search import SearchService
from data_sync_etl.main import create_app
from tests.conftest import make_pdf
from tests.search_read_path_probe import Probe, TraceMiddleware, summary
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_READPATH_PHYSICAL') != '1', reason='Owned read-path runner required')]


@pytest.fixture
def container(pg_container):
    return pg_container


def test_physical_read_path(searchable, mongo_test_database, monkeypatch):
    import torch

    torch.set_num_threads(2)
    c, first = searchable
    root = Path.cwd().resolve()
    target = Path(os.environ['ETL_READPATH_METRICS']).resolve()
    assert target.is_relative_to(root / '.test-tmp/audits/search-read-path') and not target.exists()
    model_path = Path(os.environ['ETL_QUERY_CACHE_MODEL_PATH']).resolve(strict=True)
    revision = (model_path / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    assert revision == 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
    model = LocalSentenceEmbedding(model_path,
        'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', revision, 384)
    started = perf_counter()
    model.prepare()
    cold = {'n': 1, 'model_prepare_including_import_load_probe_ms': (perf_counter() - started) * 1000,
            'scope': 'cold adapter preparation before fixture build, NOT cold HTTP latency'}
    probe = Probe()
    c.settings.search_query_embedding_cache_enabled = False
    c.settings.index_embedding_dimension = 384
    # Keep actual Search deadline and limits; never extend the production budget.
    assert c.settings.search_timeout_seconds == 15
    versions = [first]
    report = {'status': 'RUNNING', 'head': os.environ['ETL_READPATH_HEAD'],
              'scope': 'synthetic isolated PG16/Mongo8 HTTP ASGI, real MiniLM384D, NOT Dev',
              'cache': 'OFF', 'cold': cold, 'model_revision': revision,
              'requests': [], 'overhead': [], 'concurrency': [], 'parity_pairs': 0,
              'scanned_docs': 'NOT_MEASURED', 'wire_roundtrips': 'NOT_MEASURED',
              'pool_wait': 'NOT_MEASURED', 'source_hashes': {
                  str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (root / 'src/data_sync_etl').rglob('*.py')}}

    def save():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')

    with MongoClient(mongo_test_database[0], tz_aware=True, event_listeners=[probe.mongo],
                     serverSelectionTimeoutMS=3000, timeoutMS=10000) as client:
        db = client[mongo_test_database[1]]
        assert db.name.startswith('etl_test_')
        assert initialize(db, apply=True, dimension=384)['ready']
        db.mongo_content_units.insert_many(list(c.documents.units.values()))
        c.indexing.reader, c.indexing.bags = MongoContentReader(db), MongoBagStore(db, 384)
        c.indexing.embedding = model
        c.indexing.enqueue(first['id'], force=True)
        assert c.indexing.once()

        def add_resource(index, pages):
            rid = f'readpath-resource-{index}'
            with c.uow() as repo:
                resource = repo.get('master_learning_resources', first['resource_id'])
                repo.insert('master_learning_resources', {**resource, 'id': rid, 'mongo_id': None,
                            'title': 'Isolated readpath fixture ' + str(index), 'source_origin': 'LOCAL'})
            make_pdf(c, texts=[('Page Page Page fractions addition mathematics teaching. ' if index % 2 else
                                'Page Page Page motion force physics teaching. ') + ('UniqueAnchor ' if index == 2 else '')
                               + str(page) for page in range(pages)], name=f'readpath-{index}.pdf')
            version = c.ingestion.run(rid, 'input', f'readpath-{index}.pdf')['version']
            c.ocr.create_job(version['id'])
            while c.worker.once('owned-readpath-fixture'):
                pass
            # Only newly built, isolated fixture documents. No changes to Dev.
            for document in c.documents.units.values():
                if document['resource_version_id'] == version['id']:
                    db.mongo_content_units.replace_one({'_id': document['_id']}, document, upsert=True)
            c.indexing.enqueue(version['id'], force=True)
            assert c.indexing.once()
            versions.append(version)

        add_resource(2, 3)
        c.search = SearchService(c.indexing, c.settings, c.indexing.bags, query_cache_runtime=model)
        app = create_app(c)
        app.add_middleware(TraceMiddleware, probe=probe)
        cleanup = probe.install(monkeypatch, c.engine)
        serial = 0
        smoke = os.environ.get('ETL_READPATH_SMOKE') == '1'
        completion = os.environ.get('ETL_READPATH_COMPLETION') == '1'
        report['completion_run'] = completion
        report['completion_policy'] = 'New isolated fixture IDs; unfinished medium cases[61:] plus access/concurrency, no pooling response hashes across runs' if completion else None
        audit_started = perf_counter()
        with c.uow() as repo:
            resource = repo.get('master_learning_resources', first['resource_id'])
            subject = repo.find('resource_subjects', {'resource_id': first['resource_id']})[0]['subject_id']
            grade = repo.find('resource_grade_levels', {'resource_id': first['resource_id']})[0]['grade_level_id']
        try:
            with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
                corpus = 'small'
                corpus_units = 6

                def request(method, *, profile=True, label='core', query='Page', page=1, size=10,
                            filters=None, headers=None):
                    nonlocal serial
                    assert perf_counter() - audit_started < 1200, 'Audit time budget exceeded'
                    serial += 1
                    identifier = f'rp-{serial:05d}'
                    before = perf_counter()
                    response = api.post('/api/v1/search', json={
                        'query': query, 'method': method, 'page': page, 'size': size,
                        'filters': filters or {}}, headers={
                            'x-readpath-case': identifier, 'x-readpath-profile': '1' if profile else '0',
                            **(headers or {})})
                    client_ms = (perf_counter() - before) * 1000
                    row = probe.rows.pop(identifier)
                    body = response.json()
                    row.update(method=method, corpus=corpus, corpus_resources=len(versions),
                               corpus_units=corpus_units,
                               page=page, page_size=size, case=label, filter_keys=sorted(filters or {}),
                               client_total_ms=client_ms,
                               total_results=body.get('total'), returned_items=len(body.get('items', [])),
                               access_result=body.get('access_scope', body.get('detail', {}).get('code')))
                    assert row['response_hash'] == hashlib.sha256(response.content).hexdigest()
                    assert not c.settings.search_query_embedding_cache_enabled
                    report['requests'].append(row)
                    if response.status_code >= 500:
                        save()
                        print('READPATH_REQUEST_ERROR ' + str(response.status_code) + ' ' + str(row['access_result']), flush=True)
                    return response

                for size_label in (('small',) if smoke else ('medium',) if completion else ('small', 'medium')):
                    corpus = size_label
                    if corpus == 'medium':
                        for index in range(3, 7):
                            add_resource(index, 12)
                    corpus_units = db.mongo_content_units.count_documents({'is_current': True})
                    print('READPATH_CORPUS ' + corpus + ' resources=' + str(len(versions)), flush=True)
                    # Warmup excluded from summaries, actual HTTP and physical guards retained.
                    for method in ('bow', 'vector', 'hybrid'):
                        assert request(method, label='warmup').status_code == 200
                    for method in ('bow', 'vector', 'hybrid'):
                        off, on = [], []
                        for iteration in range(2 if smoke else 6):
                            responses = {}
                            for enabled in ((False, True) if iteration % 2 else (True, False)):
                                response = request(method, profile=enabled, label='aa')
                                assert response.status_code == 200
                                responses[enabled] = response.content
                                (on if enabled else off).append(report['requests'][-1]['total_ms'])
                            assert responses[True] == responses[False]
                            report['parity_pairs'] += 1
                        report['overhead'].append({'corpus': corpus, 'method': method,
                            'off_ms': summary(off), 'on_ms': summary(on),
                            'p50_percent': (summary(on)['p50'] / summary(off)['p50'] - 1) * 100})
                    # Interleave a bounded matrix, not an unbounded relevance benchmark.
                    cases = []
                    for method in ('bow', 'vector', 'hybrid'):
                        for page in (1, 2, 1, 101):
                            for size in (1, 10, 50):
                                for _ in range(2):
                                    cases.append((method, {'label': 'paging', 'page': page, 'size': size}))
                        for query_label, query in [('few', 'UniqueAnchor'), ('zero', 'zzqxnotpresent'),
                                                  ('long', 'Page ' * 55), ('independent', 'force motion physics')]:
                            for _ in range(2):
                                cases.append((method, {'label': query_label, 'query': query}))
                        for key, value in [('resource_id', first['resource_id']), ('resource_version_id', first['id']),
                                           ('subject_id', subject), ('grade_level_id', grade),
                                           ('resource_type_code', resource['resource_type_code'])]:
                            for _ in range(2):
                                cases.append((method, {'label': 'filter', 'filters': {key: value}}))
                        cases.append((method, {'label': 'filter_denied', 'filters': {'resource_id': 'missing'}}))
                    random.Random(913).shuffle(cases)
                    measured_cases = [] if smoke else cases[61:] if completion else cases
                    for index, (method, kwargs) in enumerate(measured_cases):
                        response = request(method, **kwargs)
                        # An audit records real service overload/timeouts; it does not hide them.
                        assert response.status_code in (200, 503), response.status_code
                        if index % 30 == 0:
                            save()
                            print('READPATH_MATRIX ' + corpus + ' ' + str(index + 1) + '/' + str(len(cases)), flush=True)

                    # Public fail-closed and denied local boundary: no schema/model/DB access.
                    for method in ('bow', 'vector', 'hybrid'):
                        for label, settings, headers, expected in (
                            ('public', False, {}, 200), ('denied', True, {'x-forwarded-for': '8.8.8.8'}, 403)):
                            c.settings.search_internal_dev_enabled = settings
                            for _iteration in range(2):
                                values = [request(method, profile=enabled, label=label, headers=headers)
                                          for enabled in (False, True)]
                                assert all(v.status_code == expected for v in values)
                                assert values[0].content == values[1].content
                                report['parity_pairs'] += 1
                    c.settings.search_internal_dev_enabled = True
                    # Resource-level visibility/publication withdrawal on owned fixture only.
                    with c.uow() as repo:
                        repo.update('master_learning_resources', first['resource_id'], {'publication_status': 'DRAFT'})
                    for method in ('bow', 'vector', 'hybrid'):
                        values = [request(method, profile=enabled, label='limited_resource') for enabled in (False, True)]
                        assert values[0].content == values[1].content
                        assert all(first['resource_id'] != item['resource_id'] for item in values[1].json()['items'])
                        report['parity_pairs'] += 1
                    with c.uow() as repo:
                        repo.update('master_learning_resources', first['resource_id'], {'publication_status': 'PUBLISHED'})
                    save()

                # Real concurrent HTTP requests; no sleep/barrier inside production inference.
                corpus = 'medium'
                for count in (() if smoke else (1, 2, 5, 10)):
                    for method in ('bow', 'vector', 'hybrid'):
                        for repeat in range(3):
                            barrier = Barrier(count)
                            def concurrent(index, count=count, method=method, repeat=repeat, barrier=barrier):
                                identifier = f'concurrent-{count}-{method}-{repeat}-{index}'
                                barrier.wait(timeout=30)
                                response = api.post('/api/v1/search', json={'query': 'Page', 'method': method},
                                    headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
                                return identifier, response
                            with ThreadPoolExecutor(max_workers=count) as pool:
                                observed = list(pool.map(concurrent, range(count)))
                            successes, busy, errors = [], [], []
                            for identifier, response in observed:
                                row = probe.rows.pop(identifier)
                                body = response.json()
                                row.update(method=method, corpus=corpus, corpus_resources=len(versions),
                                    corpus_units=54, page=1, page_size=10, case='concurrency', concurrency=count,
                                    total_results=body.get('total'), access_result=body.get('access_scope', body.get('detail', {}).get('code')))
                                report['requests'].append(row)
                                assert response.status_code in (200, 503)
                                if response.status_code == 503:
                                    code = body['detail']['code']
                                    assert code in ('SEARCH_RUNTIME_BUSY', 'SEARCH_TIMEOUT')
                                    (busy if code == 'SEARCH_RUNTIME_BUSY' else errors).append(row['total_ms'])
                                else:
                                    successes.append(row['total_ms'])
                            if method == 'bow':
                                assert not busy
                            report['concurrency'].append({'count': count, 'method': method, 'repeat': repeat,
                                'success_ms': summary(successes), 'busy_ms': summary(busy), 'timeout_ms': summary(errors)})
                            save()
                    print('READPATH_CONCURRENCY ' + str(count), flush=True)
                report['status'] = 'MEASURED'
                report['unexpected_5xx_count'] = sum(r['response_status'] >= 500 and
                    r.get('access_result') not in ('SEARCH_RUNTIME_BUSY',) for r in report['requests'])
                report['request_count'] = len(report['requests'])
                assert not probe.mongo.pending
                save()
        finally:
            cleanup()
