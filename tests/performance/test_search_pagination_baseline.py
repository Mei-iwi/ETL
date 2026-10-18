"""Opt-in Search-only baseline: temporary SQLite, typed Mongo double, real local384D.

No dotenv, Dev connection, downloads, Evaluation or production writes. Setup uses
existing READY fixtures; measurements contain only hashes/counters and timings.
Physical PG/Mongo and HTTP latency must be measured separately, not inferred here.
"""
import hashlib
import json
import os
import statistics
import subprocess
from collections import Counter
from pathlib import Path
from time import perf_counter, process_time

import pytest
from sqlalchemy import event

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.domain.search_access import SearchAccess
from tests.conftest import make_pdf
from tests.unit.test_search import searchable as searchable


def summarize(values):
    values = sorted(values)
    position = (len(values) - 1) * .95
    lower = int(position)
    return {'n': len(values), 'p50_ms': statistics.median(values),
            'p95_ms': values[lower] + (values[min(lower + 1, len(values) - 1)] - values[lower]) * (position - lower)}


def memory_usage():
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (field, ctypes.c_size_t) for field in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                    'PagefileUsage', 'PeakPagefileUsage')]

        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return {'rss_bytes_end': counters.WorkingSetSize, 'peak_working_set_bytes': counters.PeakWorkingSetSize}
    # Portable benchmark without an additional dependency; RSS units vary on Unix.
    return {'rss_bytes_end': None, 'peak_working_set_bytes': None}


@pytest.mark.skipif(os.environ.get('ETL_PAGINATION_BASELINE') != '1', reason='Explicit offline baseline opt-in required')
def test_real384_pagination_baseline(searchable, monkeypatch):
    import sentence_transformers
    import torch

    c, first = searchable
    model_root = Path(os.environ['ETL_PAGINATION_MODEL_PATH']).resolve()
    version = (model_root / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    assert version == 'e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
    cold = perf_counter()
    torch.set_num_threads(2)
    loads = []
    constructor = sentence_transformers.SentenceTransformer

    def load(*args, **kwargs):
        loads.append(1)
        return constructor(*args, **kwargs)

    monkeypatch.setattr(sentence_transformers, 'SentenceTransformer', load)
    model = LocalSentenceEmbedding(model_root, 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', version, 384)
    model.prepare()
    cold_ms = (perf_counter() - cold) * 1000
    assert model.ready() and len(loads) == 1
    c.indexing.embedding = model
    c.settings.index_embedding_dimension = c.indexing.bags.dimension = 384
    with c.uow() as repo:
        template = repo.get('master_learning_resources', first['resource_id'])
    versions = [first['id']]
    for number in range(1, 12):
        resource = f'pagination-fixture-{number}'
        with c.uow() as repo:
            repo.insert('master_learning_resources', {**template, 'id': resource, 'mongo_id': None, 'source_origin': 'LOCAL'})
        path = make_pdf(c, ['Page text learning math fractions addition.'] * 2, name=f'pagination-{number}.pdf')
        added = c.ingestion.run(resource, 'input', path.name)['version']
        c.ocr.create_job(added['id'])
        while c.worker.once('isolated-pagination-fixture'):
            pass
        versions.append(added['id'])
    for identifier in versions:
        c.indexing.enqueue(identifier, force=True)
        assert c.indexing.once()
    assert all(len(bag.embedding) == 384 for bag in c.indexing.bags.rows.values())

    counts = Counter()

    def wrap(obj, name, counter):
        original = getattr(obj, name)

        def counted(*args, **kwargs):
            counts[counter] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(obj, name, counted)

    import data_sync_etl.application.search as search_module
    for obj, name, counter in [
        (model, '_encode', 'encode'), (model, 'prepare', 'prepare'),
        (c.indexing.bags, 'search_candidates', 'candidate_reads_double'),
        (c.indexing.bags, 'get_bag', 'bag_reads_double'),
        (search_module, 'binary_score', 'binary_scorer_calls'),
        (search_module, 'cosine', 'vector_scorer_calls'), (search_module, 'rrf', 'rrf_calls'),
    ]:
        wrap(obj, name, counter)

    def sql_read(_connection, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith('SELECT'):
            counts['sql_selects'] += 1

    event.listen(c.engine, 'before_cursor_execute', sql_read)
    query = 'page học liệu phân số'
    access = SearchAccess('local_demo')
    try:
        for method in ('bow', 'vector', 'hybrid'):
            result = c.search.search(query, method, size=5, access=access)
            assert result['total'] == 12 and result['total_pages'] == 3
        counts.clear()
        samples, totals, response_hashes = {}, {}, {}
        start_cpu = process_time()
        for method in ('bow', 'vector', 'hybrid'):
            samples[method] = {str(step): [] for step in range(4)}
            for _ in range(30):
                for step, page in enumerate((1, 2, 1, 3)):
                    started = perf_counter()
                    response = c.search.search(query, method, page=page, size=5, access=access)
                    samples[method][str(step)].append((perf_counter() - started) * 1000)
                    signature = hashlib.sha256(json.dumps(response, sort_keys=True).encode()).hexdigest()
                    key = f'{method}:{page}'
                    assert response_hashes.setdefault(key, signature) == signature
                    assert response['total'] == 12 and response['total_pages'] == 3
                    totals[method] = {'total': response['total'], 'total_pages': response['total_pages']}
        report = {
            'status': 'MEASURED_FIXTURE_ONLY',
            'baseline_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
            'search_sha256': hashlib.sha256(Path('src/data_sync_etl/application/search.py').read_bytes()).hexdigest(),
            'scope': 'SearchService; temporary SQLite + typed Mongo double + real384D model; not HTTP/physical PG/Mongo',
            'model': {'id': model.model_id, 'revision': model.version, 'dimension': 384, 'loads': len(loads)},
            'cold_prepare_ms': cold_ms,
            'cold_scope': 'prepare only after runtime imports; OS file cache uncontrolled; one measurement',
            'steps': [1, 2, 1, 3], 'samples_per_method_step': 30,
            'requests_application': 360, 'http_requests': None,
            'counts': dict(counts), 'totals': totals, 'response_hashes': response_hashes,
            'latencies': {method: {step: summarize(values) for step, values in rows.items()}
                          for method, rows in samples.items()},
            'cpu_seconds': process_time() - start_cpu, **memory_usage(),
            'after_cache': {'status': 'NOT_RUN', 'reason': 'Safety gate: no verified cross-store corpus epoch'},
            'DEV_LIVE_NOT_RUN': True, 'GROUND_TRUTH_NOT_CONFIRMED': True, 'OAUTH_NOT_RUN': True,
        }
        assert counts['encode'] == 240 and counts['candidate_reads_double'] == 360
        assert counts['rrf_calls'] == 120 and len(loads) == 1
        output = Path(os.environ['ETL_PAGINATION_BASELINE_OUTPUT']).resolve()
        assert output.is_relative_to(Path.cwd().resolve() / 'docs/audits/search-pagination-cache')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    finally:
        event.remove(c.engine, 'before_cursor_execute', sql_read)
