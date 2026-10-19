"""Real cold preload and resident concurrent HTTP Search on owned PG/Mongo."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from threading import Barrier
from time import perf_counter, process_time

import httpx
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.api import routes
from data_sync_etl.application.search import SearchService
from data_sync_etl.main import create_app
from tests.integration.test_search_t1_physical import container as container
from tests.integration.test_search_t1_recovery import seed_medium
from tests.search_read_path_probe import Probe, summary
from tests.search_t3_support import Measures, http_server
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_T1_PHYSICAL') != '1', reason='Owned isolation runner required')]


def test_cold_start_and_resident_search(searchable, mongo_test_database, monkeypatch):
    from data_sync_etl.adapters.mongo.schema import initialize
    from tests.integration.test_http_query_cache_pg import memory

    c, first = searchable
    target = Path(os.environ['ETL_T1_OUTPUT'])
    report = {'status': 'RUNNING', 'samples': [], 'groups': [], 'source_hashes': {
        path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (
            'src/data_sync_etl/adapters/embeddings.py', 'src/data_sync_etl/application/search.py',
            'src/data_sync_etl/main.py', 'src/data_sync_etl/api/routes.py',
            'src/data_sync_etl/api/search_schemas.py')}}
    def save():
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    save()
    runtime = LocalSentenceEmbedding(os.environ['ETL_QUERY_CACHE_MODEL_PATH'],
        'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
        'e8f8c211226b894fcb81acc59f3b34ba3efd5f42', 384)
    # No torch/sentence_transformers import before the actual startup below.
    c.indexing.embedding = runtime
    c.settings.index_embedding_dimension = 384
    c.settings.search_query_embedding_cache_enabled = False
    stamp, cpu = perf_counter(), process_time()
    try:
        with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
            assert api.get('/health').status_code == 200
            status = api.get('/api/v1/search/model-status').json()
            assert status['state'] in ('LOADING', 'READY')
            report['loading_observed'] = status['state'] == 'LOADING'
            if status['state'] == 'LOADING':
                response = api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'})
                assert response.status_code == 503
                assert response.json()['detail']['code'] == 'SEARCH_MODEL_LOADING'
                assert response.headers['retry-after'] == '2'
                assert api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
                report['cold_direct_search'] = {'vector': 503, 'code': 'SEARCH_MODEL_LOADING', 'bow': 200}
            runtime._preload_thread.join(120)
            assert api.get('/api/v1/search/model-status').json() == {'state': 'READY', 'ready': True}
            report.update(load_seconds=runtime._load_seconds, warmup_seconds=runtime._warmup_seconds,
                startup_seconds=runtime._startup_seconds, wall_seconds=perf_counter() - stamp,
                startup_cpu_seconds=process_time() - cpu, load_count=runtime._load_count)
            report['process_id'] = os.getpid()
            assert len(runtime.prepare_query()('Nội dung học liệu tiếng Việt')) == 384
            import torch
            torch.set_num_threads(2)
            save()
            # Reuse that SAME resident instance to create isolated test indexes.
            probe = Probe()
            with MongoClient(mongo_test_database[0], tz_aware=True, event_listeners=[probe.mongo],
                             serverSelectionTimeoutMS=3000, timeoutMS=10000) as client:
                db = client[mongo_test_database[1]]
                assert db.name.startswith('etl_test_')
                assert initialize(db, apply=True, dimension=384)['ready']
                db.mongo_content_units.insert_many(list(c.documents.units.values()))
                c.indexing.reader, c.indexing.bags = MongoContentReader(db), MongoBagStore(db, 384)
                c.indexing.enqueue(first['id'], force=True)
                assert c.indexing.once()
                seed_medium(c, first, db)
                c.search = SearchService(c.indexing, c.settings, c.indexing.bags)
                cleanup = probe.install(monkeypatch, c.engine)
                metrics = Measures(probe)
                clean_metrics = metrics.install(c, monkeypatch)
                serial = iter(range(10000))
                try:
                    with ExitStack() as stack:
                        url = stack.enter_context(http_server(routes.search_materials, c, probe, metrics))
                        clients = [stack.enter_context(httpx.Client(base_url=url, timeout=18, trust_env=False)) for _ in range(5)]
                        def request(i, method, gate=None):
                            identifier = 'cold-warm-' + str(next(serial))
                            metrics.register(identifier)
                            if gate is not None:
                                gate.wait(timeout=10)
                            start = perf_counter()
                            response = clients[i].post('/api/v1/search', json={'query': 'Page fractions addition', 'method': method, 'size': 2},
                                headers={'x-readpath-case': identifier, 'x-readpath-profile': '0'})
                            elapsed = (perf_counter() - start) * 1000
                            measured = metrics.take(identifier)
                            report['samples'].append({'method': method, 'status': response.status_code,
                                'wall_ms': elapsed, **measured})
                            assert response.status_code == 200, response.json().get('detail', {}).get('code')
                            assert (measured['SQL'], measured['text_SELECT'], measured['Mongo']) == (110, 12, 67)
                            return response.content, elapsed
                        references = {m: request(0, m)[0] for m in ('bow', 'vector', 'hybrid')}
                        with ThreadPoolExecutor(max_workers=5) as pool:
                            for n in (1, 2, 3, 5):
                                for method in references:
                                    times = []
                                    start, cpu = perf_counter(), process_time()
                                    for _ in range(10):
                                        gate = Barrier(n)
                                        jobs = [pool.submit(request, i, method, gate) for i in range(n)]
                                        for job in jobs:
                                            content, elapsed = job.result(timeout=20)
                                            assert content == references[method]
                                            times.append(elapsed)
                                    report['groups'].append({'clients': n, 'method': method,
                                        'n': len(times), **summary(times),
                                        'wall_seconds': perf_counter() - start, 'cpu_seconds': process_time() - cpu,
                                        **memory()})
                                    save()
                        assert runtime._load_count == 1
                        assert metrics.max_encoders == 1
                        report.update(status='PASS', load_count=runtime._load_count,
                                      max_encoders=metrics.max_encoders, dimension=384)
                finally:
                    clean_metrics()
                    cleanup()
                save()
    finally:
        runtime.stop_preload()
