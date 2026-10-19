"""Gate1 baseline smoke; owned PostgreSQL/MongoDB and unchanged real MiniLM384D."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from sqlalchemy import event

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search import SearchError
from data_sync_etl.main import create_app
from tests.conftest import make_pdf
from tests.search_read_path_probe import Probe, TraceMiddleware
from tests.search_t1_support import FailureCheckpoint
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_T1_PHYSICAL') != '1', reason='Explicit owned T1 runner required')]


@pytest.fixture
def container(pg_container):
    return pg_container


@pytest.fixture
def physical_search(searchable, mongo_test_database, monkeypatch):
    import torch

    torch.set_num_threads(2)
    c, version = searchable
    c.settings.index_embedding_dimension = 384
    c.settings.search_query_embedding_cache_enabled = False
    path = Path(os.environ['ETL_QUERY_CACHE_MODEL_PATH']).resolve(strict=True)
    revision = (path / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    assert revision == 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
    c.indexing.embedding = LocalSentenceEmbedding(path,
        'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', revision, 384)
    probe = Probe()
    with MongoClient(mongo_test_database[0], tz_aware=True, event_listeners=[probe.mongo],
                     serverSelectionTimeoutMS=3000, timeoutMS=10000) as client:
        db = client[mongo_test_database[1]]
        assert db.name.startswith('etl_test_')
        assert initialize(db, apply=True, dimension=384)['ready']
        db.mongo_content_units.insert_many(list(c.documents.units.values()))
        c.indexing.reader, c.indexing.bags = MongoContentReader(db), MongoBagStore(db, 384)
        c.indexing.enqueue(version['id'], force=True)
        assert c.indexing.once()
        c.search = SearchService(c.indexing, c.settings, c.indexing.bags)
        c._t1_probe = probe
        cleanup = probe.install(monkeypatch, c.engine)
        try:
            yield c, version, db
        finally:
            cleanup()


def test_t5_baseline_preflight(physical_search, monkeypatch):
    c, version, _db = physical_search
    c.search = BaselineSearch(c.indexing, c.settings, c.indexing.bags)
    target = Path(os.environ['ETL_T1_OUTPUT'])
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    rows = []
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        def request(method, *, simulated=False, expected=None):
            started = perf_counter()
            response = api.post('/api/v1/search', json={'query': 'page', 'method': method})
            duration = (perf_counter() - started) * 1000
            failures.observe(response, method=method, request_id=len(rows) + 1,
                duration_ms=duration, fixture='small', simulated=simulated, expected=expected)
            rows.append({'method': method, 'status': response.status_code, 'duration_ms': duration,
                         'simulated': simulated})
            return response
        for method in ('bow', 'vector', 'hybrid'):
            assert request(method).status_code == 200
        assert c.search._http_model_slot.acquire(blocking=False)
        try:
            for method in ('vector', 'hybrid'):
                assert request(method).status_code == 503
            assert request('bow').status_code == 200
        finally:
            c.search._http_model_slot.release()
        assert request('hybrid').status_code == 200
        # Explicitly SIMULATED non-busy failure only to verify safe classification/persistence.
        def timeout(_started):
            raise SearchError('SEARCH_TIMEOUT')
        with monkeypatch.context() as patch:
            patch.setattr(c.search, '_deadline', timeout)
            assert request('bow', simulated=True, expected='SEARCH_TIMEOUT').status_code == 503
    assert [v['code'] for v in failures.events] == ['SEARCH_RUNTIME_BUSY', 'SEARCH_RUNTIME_BUSY', 'SEARCH_TIMEOUT']
    assert all(v['expected'] for v in failures.events)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({'status': 'PREFLIGHT_PASS', 'cache': 'OFF',
        'head': os.environ['ETL_READPATH_HEAD'], 'rows': rows,
        'unexpected_5xx': 0, 'simulated_nonbusy': 1,
        'scope': 'owned PG/Mongo, synthetic local launch, real384D; old503 root cause unknown'}, indent=2) + '\n', encoding='utf-8')


class BaselineSearch(SearchService):
    """Test control uses unchanged default _snapshot point reads, no fake results."""
    def search(self, *args, **kwargs):
        kwargs['_batch_texts'] = False
        return super().search(*args, **kwargs)


class CandidateSearch(SearchService):
    """Explicit candidate for paired tests, independent of the HTTP entrypoint."""
    def search(self, *args, **kwargs):
        kwargs['_batch_texts'] = True
        return super().search(*args, **kwargs)


def test_t1_acceptance(physical_search):
    c, version, db = physical_search
    probe = c._t1_probe
    target = Path(os.environ['ETL_T1_OUTPUT'])
    failures = FailureCheckpoint(target.with_name(target.stem + '_FAILURES.json'))
    report = {'status': 'RUNNING', 'cache': 'OFF', 'head': os.environ['ETL_READPATH_HEAD'],
              'stable': [], 'races': [], 'benchmark': 'NOT_RUN_UNTIL_CORRECTNESS_PASSES'}
    def save():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    # Reproduce the audited physical 6-resource/54unit/54Bag cardinality, never Dev.
    for index, pages in ((2, 3), (3, 12), (4, 12), (5, 12), (6, 12)):
        rid = f't1-resource-{index}'
        with c.uow() as repo:
            source = repo.get('master_learning_resources', version['resource_id'])
            repo.insert('master_learning_resources', {**source, 'id': rid, 'mongo_id': None,
                        'title': 'Isolated T1 ' + str(index), 'source_origin': 'LOCAL'})
        make_pdf(c, ['Page fractions addition learning ' + str(n) for n in range(pages)], name=f't1-{index}.pdf')
        created = c.ingestion.run(rid, 'input', f't1-{index}.pdf')['version']
        c.ocr.create_job(created['id'])
        while c.worker.once('t1-isolated-seed'):
            pass
        for doc in c.documents.units.values():
            if doc['resource_version_id'] == created['id']:
                db.mongo_content_units.replace_one({'_id': doc['_id']}, doc, upsert=True)
        c.indexing.enqueue(created['id'], force=True)
        assert c.indexing.once()
    assert db.content_bags.count_documents({'is_current': True}) == 54
    baseline = BaselineSearch(c.indexing, c.settings, c.indexing.bags)
    candidate = CandidateSearch(c.indexing, c.settings, c.indexing.bags)
    app = create_app(c)
    app.add_middleware(TraceMiddleware, probe=probe)
    serial = 0
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        def request(service, method='bow', *, label):
            nonlocal serial
            serial += 1
            identifier = 't1-' + str(serial)
            c.search = service
            response = api.post('/api/v1/search', json={'query': 'Page', 'method': method},
                headers={'x-readpath-case': identifier, 'x-readpath-profile': '1'})
            row = probe.rows.pop(identifier)
            failures.observe(response, method=method, request_id=serial, duration_ms=row['total_ms'], fixture='medium')
            report[label].append({'path': 'baseline' if service is baseline else 'batch',
                'method': method, 'status': response.status_code, 'total': response.json().get('total'),
                'code': response.json().get('detail', {}).get('code'), 'response_hash': row['response_hash'],
                'PG_exec_count': row['PG_exec_count'], 'Mongo_cmd_count': row['Mongo_cmd_count'],
                'text_SELECTs': sum(v['backend'] == 'pg' and v.get('tables') == ['content_unit_texts'] for v in row['operations']),
                'total_ms': row['total_ms']})
            save()
            return response
        for method in ('bow', 'vector', 'hybrid'):
            before = request(baseline, method, label='stable')
            after = request(candidate, method, label='stable')
            assert before.status_code == after.status_code == 200
            same_http_bytes = before.content == after.content
            assert same_http_bytes, 'Stable HTTP body parity failed (payload not logged)'
            assert report['stable'][-2]['PG_exec_count'] == 206
            assert report['stable'][-1]['PG_exec_count'] == 110
            assert report['stable'][-2]['text_SELECTs'] == 108
            assert report['stable'][-1]['text_SELECTs'] == 12
            assert report['stable'][-2]['Mongo_cmd_count'] == report['stable'][-1]['Mongo_cmd_count'] == 355
        print('T1_STABLE_PARITY_PASS SQL206->110 TEXT108->12 MONGO355->355', flush=True)

        # Deterministic READ COMMITTED race: after the first text statement returns,
        # an independent writer changes a later unit, without a Mongo/PG-marker update.
        # Do not assume that atomic batch timing has identical outcomes to point reads.
        with c.uow() as repo:
            units = repo.find('content_units', {'resource_version_id': version['id']}, order=('sequence_no', 'id'))
            last = units[-1]['id']
            old = repo.get('content_unit_texts', last)['text']
        outputs = []
        for service in (baseline, candidate):
            entered, release = Event(), Event()
            fired = [False]
            def after_text(_conn, _cursor, statement, _params, _ctx, _many,
                           entered=entered, release=release, fired=fired):
                if (not fired[0] and statement.lstrip().upper().startswith('SELECT')
                        and 'FROM content_unit_texts' in statement):
                    fired[0] = True
                    entered.set()
                    assert release.wait(15), 'Writer barrier timed out'
            event.listen(c.engine, 'after_cursor_execute', after_text)
            try:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(request, service, label='races')
                    try:
                        assert entered.wait(20), 'Text query barrier not reached'
                        with c.uow() as repo:
                            repo.update('content_unit_texts', last, {'text': 'Changed by isolated writer after first text SELECT'})
                    finally:
                        release.set()
                    response = pending.result(timeout=30)
                outputs.append((response.status_code, hashlib.sha256(response.content).hexdigest()))
            finally:
                event.remove(c.engine, 'after_cursor_execute', after_text)
                with c.uow() as repo:
                    repo.update('content_unit_texts', last, {'text': old})
        if outputs[0] != outputs[1]:
            report['status'] = 'BLOCKED_RACE_OUTCOME_PARITY'
            report['reason'] = 'Atomic batch versus staggered point reads produces different HTTP outcome under deterministic writer barrier'
            save()
        assert outputs[0] == outputs[1], 'T1 race outcome differs from baseline; do not commit'
        report['status'] = 'PARTIAL_MORE_ACCEPTANCE_REQUIRED'
        save()
