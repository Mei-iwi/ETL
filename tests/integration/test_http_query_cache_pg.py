"""Paired real-model Search over owned isolated PG/Mongo only; opt-in, no dotenv."""
import ctypes
import hashlib
import json
import os
import statistics
from collections import Counter
from copy import deepcopy
from pathlib import Path
from time import perf_counter, process_time

import pytest
from pymongo import MongoClient, monitoring
from sqlalchemy import event

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.application.http_query_cache import COUNTERS
from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search_access import SearchAccess
from tests.conftest import make_pdf
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_QUERY_CACHE_PHYSICAL') != '1', reason='Explicit owned PG/Mongo/model benchmark required')]
ACCESS = SearchAccess('local_demo')


@pytest.fixture
def container(pg_container):
    return pg_container


def summary(values):
    values = sorted(values)
    position = (len(values) - 1) * .95
    low = int(position)
    return {'n': len(values), 'p50_ms': statistics.median(values),
            'p95_ms': values[low] + (values[min(low + 1, len(values) - 1)] - values[low]) * (position - low)}


def memory():
    if os.name != 'nt':
        return {'rss_bytes': None, 'peak_rss_bytes': None}
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ('peak', 'working', 'pp', 'p', 'pnp', 'np', 'pf', 'peakpf')]
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
    api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Memory), wintypes.DWORD]
    counters = Memory()
    counters.cb = ctypes.sizeof(counters)
    assert api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return {'rss_bytes': counters.working, 'peak_rss_bytes': counters.peak}


def test_real_pg_mongo_paired_query_cache_and_live_changes(searchable, mongo_test_database, monkeypatch):
    import sentence_transformers
    import torch

    torch.set_num_threads(2)
    c, first = searchable
    owner = ['setup']
    counts = {name: Counter() for name in ('setup', 'off', 'on', 'verification')}

    class Commands(monitoring.CommandListener):
        def started(self, event):
            counts[owner[0]]['mongo_' + event.command_name] += 1

        def succeeded(self, event):
            pass

        def failed(self, event):
            pass

    loads = []
    constructor = sentence_transformers.SentenceTransformer

    def construct(*args, **kwargs):
        loads.append(1)
        return constructor(*args, **kwargs)

    monkeypatch.setattr(sentence_transformers, 'SentenceTransformer', construct)
    encode, prepare = LocalSentenceEmbedding._encode, LocalSentenceEmbedding.prepare

    def encoded(self, text):
        counts[owner[0]]['query_or_probe_encode'] += 1
        if not self._checked:
            counts[owner[0]]['readiness_encode'] += 1
        started = perf_counter()
        try:
            return encode(self, text)
        finally:
            counts[owner[0]]['encode_seconds'] += perf_counter() - started

    def prepared(self):
        counts[owner[0]]['prepare'] += 1
        started = perf_counter()
        try:
            return prepare(self)
        finally:
            counts[owner[0]]['prepare_seconds'] += perf_counter() - started

    monkeypatch.setattr(LocalSentenceEmbedding, '_encode', encoded)
    monkeypatch.setattr(LocalSentenceEmbedding, 'prepare', prepared)
    model_path = Path(os.environ['ETL_QUERY_CACHE_MODEL_PATH']).resolve()
    revision = (model_path / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    assert revision == 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
    model = LocalSentenceEmbedding(model_path, 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', revision, 384)
    cold_started = perf_counter()
    model.prepare()
    cold_prepare_ms = (perf_counter() - cold_started) * 1000
    assert model.ready() and len(loads) == 1
    with c.uow() as repo:
        template = repo.get('master_learning_resources', first['resource_id'])
    versions = [first['id']]
    for number in range(1, 12):
        identifier = f'cache-paired-fixture-{number}'
        with c.uow() as repo:
            repo.insert('master_learning_resources', {**template, 'id': identifier, 'mongo_id': None, 'source_origin': 'LOCAL'})
        path = make_pdf(c, ['Page text learning math fractions addition.'] * 2, name=f'paired-{number}.pdf')
        version = c.ingestion.run(identifier, 'input', path.name)['version']
        c.ocr.create_job(version['id'])
        while c.worker.once('isolated-query-cache-fixture'):
            pass
        versions.append(version['id'])
    with MongoClient(mongo_test_database[0], tz_aware=True, event_listeners=[Commands()],
                     serverSelectionTimeoutMS=3000, timeoutMS=10000) as client:
        database = client[mongo_test_database[1]]
        assert database.name.startswith('etl_test_')
        assert initialize(database, apply=True, dimension=384)['ready']
        database.mongo_content_units.insert_many(list(c.documents.units.values()))
        c.indexing.reader, c.indexing.bags = MongoContentReader(database), MongoBagStore(database, 384)
        c.indexing.embedding = model
        c.settings.index_embedding_dimension = 384
        for version_id in versions:
            c.indexing.enqueue(version_id, force=True)
            assert c.indexing.once()
        services = {name: SearchService(c.indexing, c.settings.model_copy(update={
            'search_query_embedding_cache_enabled': name == 'on'}), c.indexing.bags, query_cache_runtime=model)
                    for name in ('off', 'on')}
        query = 'page học liệu phân số'

        def request(label, method, page, size=5):
            owner[0] = label
            with services[label].runtime_admission(method, access=ACCESS):
                return services[label].search_http(query, method, page=page, size=size, access=ACCESS)

        # Cold Search calls (model already provisioned/prepared) reported separately.
        first_search_ms = {}
        for label in ('off', 'on'):
            started = perf_counter()
            assert request(label, 'vector', 1)['total'] == 12
            first_search_ms[label] = (perf_counter() - started) * 1000
        samples = {label: {method: {str(step): [] for step in range(4)}
                          for method in ('bow', 'vector', 'hybrid')} for label in services}
        for label in services:
            counts[label].clear()
            services[label]._http_query_vectors.clear()
        cache_before = services['on']._http_query_vectors.stats()

        def sql(_connection, _cursor, statement, *_args):
            if statement.lstrip().upper().startswith('SELECT'):
                counts[owner[0]]['sql_selects'] += 1

        event.listen(c.engine, 'before_cursor_execute', sql)
        hashes, cpu = {}, Counter()
        repeats = int(os.environ.get('ETL_QUERY_CACHE_PAIRS', '30'))
        assert 1 <= repeats <= 30
        owner[0] = 'verification'  # Inventory reads are not measured Search reads.
        before = {name: list(database[name].find({}).sort('_id', 1)) for name in database.list_collection_names()}
        try:
            for method in ('bow', 'vector', 'hybrid'):
                for repeat in range(repeats):
                    for step, page in enumerate((1, 2, 1, 3)):
                        outputs = {}
                        order = ('off', 'on') if (repeat + step) % 2 == 0 else ('on', 'off')
                        for label in order:
                            cpu_started, started = process_time(), perf_counter()
                            outputs[label] = request(label, method, page)
                            samples[label][method][str(step)].append((perf_counter() - started) * 1000)
                            cpu[label] += process_time() - cpu_started
                            counts[label]['application_requests'] += 1
                            assert outputs[label]['total'] == 12 and outputs[label]['total_pages'] == 3
                        assert outputs['on'] == outputs['off']
                        signature = hashlib.sha256(json.dumps(outputs['off'], sort_keys=True).encode()).hexdigest()
                        key = f'{method}:{page}'
                        assert hashes.setdefault(key, signature) == signature
            owner[0] = 'verification'
            assert before == {name: list(database[name].find({}).sort('_id', 1)) for name in before}
            assert counts['off']['query_or_probe_encode'] == 8 * repeats
            assert counts['on']['query_or_probe_encode'] < 8 * repeats
            assert counts['on']['sql_selects'] == counts['off']['sql_selects']
            assert counts['on']['mongo_find'] == counts['off']['mongo_find']
            assert len(loads) == 1 and model.dimension == 384
            measured_counts = {name: dict(counts[name]) for name in services}
            cache_stats = services['on']._http_query_vectors.stats()
            assert cache_stats['entries'] <= 64 and cache_stats['accounted_bytes'] <= 1024 * 1024
            report = {
                'scope': 'paired SearchService+admission, real isolated PostgreSQL/MongoDB + local384D; not Dev/HTTP latency',
                'control_head': 'da24eaec59f905868e47f9115f74809c92c7d80b',
                'baseline_overwritten': False, 'resources': 12, 'steps': [1, 2, 1, 3],
                'measurement_status': 'PAIRED_BENCHMARK' if repeats == 30 else 'SMOKE_NOT_BENCHMARK',
                'samples_per_label_method_step': repeats, 'paired_order': 'alternating by repeat+step',
                'model': {'id': model.model_id, 'revision': model.version, 'dimension': 384, 'loads': len(loads)},
                'cold_prepare_ms': cold_prepare_ms, 'first_search_ms_after_prepare': first_search_ms,
                'latencies': {label: {method: {step: summary(values) for step, values in rows.items()}
                                     for method, rows in methods.items()} for label, methods in samples.items()},
                'counts': measured_counts, 'cpu_seconds': dict(cpu), 'memory': memory(), 'cache': cache_stats,
                'cache_measured': {key: cache_stats[key] - cache_before[key] for key in COUNTERS},
                'paired_deltas_ms': {method: {step: {
                    **summary([off - on for off, on in zip(samples['off'][method][step],
                                                         samples['on'][method][step], strict=True)]),
                    'on_faster_pairs': sum(off > on for off, on in zip(samples['off'][method][step],
                                                                      samples['on'][method][step], strict=True)),
                } for step in samples['off'][method]} for method in samples['off']},
                'query_encodes_saved': counts['off']['query_or_probe_encode'] - counts['on']['query_or_probe_encode'],
                'exact_response_parity': True, 'responses_sha256': hashes,
                'DEV_LIVE_NOT_RUN': True, 'OAUTH_NOT_RUN': True, 'GROUND_TRUTH_NOT_CONFIRMED': True,
            }
            target = Path(os.environ['ETL_QUERY_CACHE_METRICS']).resolve()
            assert target.is_relative_to(Path.cwd().resolve() / '.test-tmp/audits/search-query-vector-cache')
            target.parent.mkdir(parents=True, exist_ok=True)
            report['live_mutation_status'] = 'NOT_RUN'
            report['code_sha256'] = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (
                'src/data_sync_etl/application/http_query_cache.py',
                'src/data_sync_etl/application/search.py', 'src/data_sync_etl/api/routes.py')}
            target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        finally:
            event.remove(c.engine, 'before_cursor_execute', sql)
        owner[0] = 'verification'
        # New highest-ranked READY resource while the query vector is already cached.
        identifier = 'cache-new-top-resource'
        with c.uow() as repo:
            repo.insert('master_learning_resources', {**template, 'id': identifier, 'mongo_id': None, 'source_origin': 'LOCAL'})
        path = make_pdf(c, ['Page learning math added resource'], name='new-top.pdf')
        added = c.ingestion.run(identifier, 'input', path.name)['version']
        c.ocr.create_job(added['id'])
        while c.worker.once('isolated-query-cache-fixture'):
            pass
        database.mongo_content_units.insert_many([
            doc for doc in c.documents.units.values() if doc['resource_version_id'] == added['id']])
        c.indexing.enqueue(added['id'], force=True)
        assert c.indexing.once()
        query_vector = model.embed(query)
        database.content_bags.update_many({'resource_version_id': added['id']}, {'$set': {'embedding': query_vector}})
        for method in ('bow', 'vector', 'hybrid'):
            assert request('on', method, 1) == request('off', method, 1)
        assert request('on', 'vector', 1)['items'][0]['resource_id'] == identifier
        first_bag = database.content_bags.find_one({'resource_version_id': first['id']})
        original_bag = deepcopy(first_bag)
        for change in ({'embedding': [0.5] * 384}, {'is_current': False}):
            database.content_bags.update_one({'_id': first_bag['_id']}, {'$set': change})
            assert request('on', 'hybrid', 1) == request('off', 'hybrid', 1)
            database.content_bags.replace_one({'_id': first_bag['_id']}, original_bag)
        database.content_bags.delete_one({'_id': first_bag['_id']})
        assert request('on', 'vector', 1) == request('off', 'vector', 1)
        with c.uow() as repo:
            repo.update('master_learning_resources', added['resource_id'], {'is_active': False})
        for page, size in ((1, 5), (2, 10), (3, 20), (99, 5)):
            assert request('on', 'vector', page, size) == request('off', 'vector', page, size)
        report['live_mutation_parity'] = True
        report['live_mutation_status'] = 'PASS'
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
