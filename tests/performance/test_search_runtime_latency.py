"""Search-only paired regression: no Evaluation calls, no live data or downloads."""
import hashlib
import importlib.util
import json
import os
import statistics
import subprocess
from collections import Counter
from pathlib import Path
from time import perf_counter

import pytest

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.application.search import SearchService
from data_sync_etl.config import Settings
from data_sync_etl.domain.search_access import SearchAccess
from tests.conftest import make_pdf
from tests.unit.test_search import searchable as searchable

QUERIES = ('page text', 'math', 'science', 'learning', 'phân số', 'học liệu', 'tiếng Việt',
           'định luật', 'lịch sử', 'toán học', 'no results xyz', '  page text  ', 'Vie\u0323̂t',
           'cộng trừ', 'page text', 'math', 'science', 'học liệu')
BASE = '058bd6f5d45ed33126e9c7d9bd5676f97c03f87e'
ACCESS = SearchAccess('local_demo')


def summarize(values):
    ordered = sorted(values)
    position = (len(ordered) - 1) * .95
    lower = int(position)
    return {'n': len(values), 'median_ms': statistics.median(values),
            'p95_ms': ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)}


@pytest.mark.skipif(os.environ.get('ETL_SEARCH_RUNTIME_BENCHMARK') != '1', reason='Explicit offline Search benchmark required')
def test_search_only_paired_runtime_latency(searchable, tmp_path, monkeypatch):
    c, first = searchable
    versions = [first['id']]
    with c.uow() as repo:
        template = repo.get('master_learning_resources', first['resource_id'])
    # Normal isolated fixture setup only; no Dev OCR/Indexing work.
    for number in range(1, 6):
        rid = f'search-benchmark-{number}'
        with c.uow() as repo:
            repo.insert('master_learning_resources', {**template, 'id': rid, 'mongo_id': None, 'source_origin': 'LOCAL'})
        path = make_pdf(c, [f'Page text math science learning topic {number}'] * 2, name=f'search-{number}.pdf')
        version = c.ingestion.run(rid, 'input', path.name)['version']
        c.ocr.create_job(version['id'])
        while c.worker.once('isolated-search-fixture'):
            pass
        versions.append(version['id'])

    cold_started = perf_counter()
    import sentence_transformers
    import torch

    torch.set_num_threads(2)
    loads = []
    constructor = sentence_transformers.SentenceTransformer

    def load(*args, **kwargs):
        loads.append(1)
        return constructor(*args, **kwargs)

    monkeypatch.setattr(sentence_transformers, 'SentenceTransformer', load)
    config = Settings(_env_file=None)
    model = LocalSentenceEmbedding(config.index_embedding_model_path, config.index_embedding_model_id,
                                  config.index_embedding_model_version, config.index_embedding_dimension)
    model.prepare()
    cold_ms = (perf_counter() - cold_started) * 1000
    assert model.dimension == 384 and len(loads) == 1
    c.indexing.embedding = model
    c.settings.index_embedding_dimension = c.indexing.bags.dimension = 384
    for version_id in versions:
        c.indexing.enqueue(version_id, force=True)
        assert c.indexing.once()

    path = 'src/data_sync_etl/application/search.py'
    source = subprocess.run(['git', 'show', f'{BASE}:{path}'], check=True, capture_output=True).stdout
    baseline = tmp_path / 'baseline_search.py'
    baseline.write_bytes(source)
    spec = importlib.util.spec_from_file_location('search_runtime_baseline', baseline)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    services = {'before': module.SearchService(c.indexing, c.settings, c.indexing.bags),
                'after': SearchService(c.indexing, c.settings, c.indexing.bags)}
    calls = Counter()
    original = model._encode
    owner = ['warmup']

    def encode(text):
        calls[owner[0]] += 1
        return original(text)

    monkeypatch.setattr(model, '_encode', encode)
    for service in services.values():
        for method in ('bow', 'vector', 'hybrid'):
            service.search('page text', method, size=5, access=ACCESS)
    samples = {label: {method: [] for method in ('bow', 'vector', 'hybrid')} for label in services}
    result_hashes = []
    for repeat in range(3):
        order = ('before', 'after') if repeat % 2 == 0 else ('after', 'before')
        for query in QUERIES:
            for method in ('bow', 'vector', 'hybrid'):
                outputs = []
                for label in order:
                    owner[0] = label
                    started = perf_counter()
                    result = services[label].search(query, method, size=5, access=ACCESS)
                    samples[label][method].append((perf_counter() - started) * 1000)
                    outputs.append(result)
                assert outputs[0] == outputs[1]  # Full response, including source/context IDs; tolerance0.
                result_hashes.append(hashlib.sha256(json.dumps(outputs[0], sort_keys=True).encode()).hexdigest())
    assert calls['before'] == calls['after'] == 108  # Independent Vector/Hybrid still encode each query.
    assert len(loads) == 1
    report = {'base': BASE, 'baseline_search_sha256': hashlib.sha256(source).hexdigest(),
              'current_search_sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
              'scope': 'same SQLite/typed Bag fixture and real local model; SearchService only, not Evaluation or physical PG/Mongo',
              'versions': len(versions), 'bags': len(c.indexing.bags.rows), 'queries': len(QUERIES), 'repeats': 3,
              'model_id': model.model_id, 'revision': model.version, 'dimension': model.dimension, 'model_loads': len(loads),
              'cold_prepare_ms': cold_ms, 'cold_scope': 'imports+weights+probe; one measurement, OS cache uncontrolled; adapter unchanged',
              'search': {label: {method: summarize(values) for method, values in methods.items()} for label, methods in samples.items()},
              'encode_calls': {label: calls[label] for label in services}, 'full_response_parity': True, 'tolerance': 0,
              'results_sha256': hashlib.sha256(json.dumps(result_hashes).encode()).hexdigest()}
    output = os.environ.get('ETL_SEARCH_RUNTIME_OUTPUT')
    if output:
        target = Path(output).resolve()
        assert target.is_relative_to(Path.cwd().resolve())
        target.parent.mkdir(exist_ok=True, parents=True)
        target.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
