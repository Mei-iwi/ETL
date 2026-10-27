"""P07 ASGI throughput matrix: SQLite/memory stores, 3D double, NOT real BGE."""
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import perf_counter, process_time

import pytest
from sqlalchemy import event

from data_sync_etl.adapters.concept_corpus import ReadOnlyMongoEvidenceReader
from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker
from data_sync_etl.main import create_app
from scripts.benchmark_concept_artifacts import peak_rss, quantiles
from tests.local_admin_client import TestClient
from tests.unit.test_dynamic_concept_dictionary import activate, approve
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('searchable', [{'texts': ['Cache makes repeated data lookups faster.']}], indirect=True)
def test_isolated_http_modes_flags_cache_and_concurrency(searchable, pipeline, tmp_path, monkeypatch):
    container, _ = searchable
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    container.search.concept_resolver = provider
    queries = ['cache', 'bộ nhớ đệm', 'mémoire tampon', 'bo nho dem', 'Explain ' * 35 + 'cache', 'unrelatedzznonexistent']
    guard_counts = {'builder': 0, 'mongo_corpus': 0}

    def forbidden_builder(*args, **kwargs):
        guard_counts['builder'] += 1
        pytest.fail('Builder entered HTTP')

    def forbidden_reader(*args, **kwargs):
        guard_counts['mongo_corpus'] += 1
        pytest.fail('Mongo corpus reader entered HTTP')

    monkeypatch.setattr(ConceptDiscoveryWorker, 'build', forbidden_builder)
    monkeypatch.setattr(ConceptDiscoveryWorker, 'tick', forbidden_builder)
    monkeypatch.setattr(ReadOnlyMongoEvidenceReader, '_cursor', forbidden_reader)
    calls, sql_lock = [0], Lock()

    def statement(*args):
        with sql_lock:
            calls[0] += 1

    event.listen(container.engine, 'before_cursor_execute', statement)
    results, events, cpu = [], [], process_time()
    try:
        with TestClient(create_app(container)) as api:
            assert api.post('/api/v1/search', json={'query': 'cache', 'method': 'bow'}).status_code == 200
            for enabled in (False, True):
                provider.enabled = enabled
                for method in ('bow', 'vector', 'hybrid'):
                    for cache in ('cold_dictionary', 'warm_dictionary'):
                        for concurrency in (1, 2, 4):
                            with provider._lock:
                                provider._cache.clear()
                                provider._bytes = 0
                            if cache == 'warm_dictionary' and method != 'vector':
                                for query in queries:
                                    with provider.pin(query):
                                        pass
                            before = calls[0]

                            def request(index, _method=method):
                                started = perf_counter()
                                response = api.post('/api/v1/search', json={'query': queries[index], 'method': _method})
                                elapsed = (perf_counter() - started) * 1000
                                assert response.status_code in (200, 429, 503)
                                return {'qid': index, 'status': response.status_code, 'ms': elapsed}

                            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                                samples = list(pool.map(request, range(len(queries))))
                            label = {'dynamic': enabled, 'method': method, 'cache': cache, 'concurrency': concurrency}
                            results.append({**label, 'latency_ms': quantiles([sample['ms'] for sample in samples]),
                                'errors': sum(sample['status'] != 200 for sample in samples), 'sqlite_calls': calls[0] - before})
                            events.extend({**label, **sample} for sample in samples)
    finally:
        event.remove(container.engine, 'before_cursor_execute', statement)
        metrics = provider.status()
        provider.close()
    assert guard_counts == {'builder': 0, 'mongo_corpus': 0}
    report = {'environment': 'ISOLATED_ASGI_SQLITE_MEMORY_3D_ENCODER_DOUBLE', 'real_bge': False,
        'samples_per_cell': 6, 'quality_status': 'UNJUDGED', 'results': results, 'events': events,
        'guard_counts': guard_counts, 'dictionary_metrics': metrics, 'cpu_seconds': process_time() - cpu,
        'peak_rss_bytes': peak_rss(), 'builder_pressure': 'NOT_MEASURED_LIVE', 'warmup_excluded': 1}
    (tmp_path / 'http-concurrency.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
