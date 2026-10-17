"""Opt-in paired comparison with trusted, frozen repository code on ONE fixture."""
import hashlib
import importlib.util
import subprocess
import sys
from collections import defaultdict
from functools import partial
from time import perf_counter

from data_sync_etl.adapters.storage.evaluation_store import EvaluationFileStore
from data_sync_etl.domain.evaluation import checksum
from data_sync_etl.domain.search_access import SearchAccess

BASE = '9437969'
ACCESS = SearchAccess('local_demo')


def compare(c, evaluation, command, run_command, queries, tmp_path, monkeypatch, probe_type, summarize, observed):
    modules, hashes = {}, {}
    for name, path in (
        ('embeddings', 'src/data_sync_etl/adapters/embeddings.py'),
        ('search', 'src/data_sync_etl/application/search.py'),
        ('evaluation', 'src/data_sync_etl/application/evaluation.py'),
    ):
        # This reads repository code only; it never changes Git state or uses Dev data.
        source = subprocess.run(['git', 'show', f'{BASE}:{path}'], check=True, capture_output=True).stdout
        hashes[path] = hashlib.sha256(source).hexdigest()
        file = tmp_path / f'baseline_{name}.py'
        file.write_bytes(source)
        spec = importlib.util.spec_from_file_location(f'performance_baseline_{name}', file)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, module)
        spec.loader.exec_module(module)
        modules[name] = module
    current = c.indexing.embedding
    if hasattr(current, '_model'):
        old_model = modules['embeddings'].LocalSentenceEmbedding(
            current.path, current.model_id, current.version, current.dimension)
        # Identical already-loaded, read-only weights for alternating inference.
        # Cold load is measured separately, never claimed from this comparison.
        old_model._model, old_model._checked = current._model, current._checked
    else:
        from tests.indexing_support import ModelDouble
        old_model = ModelDouble()
    old_search = modules['search'].SearchService(c.indexing, c.settings, c.indexing.bags)
    old_search.search = partial(old_search.search, access=ACCESS)
    old_eval = modules['evaluation'].EvaluationService(old_search,
        EvaluationFileStore(tmp_path / 'paired-baseline'), c.settings)
    variants = {'before': (old_model, old_search, old_eval), 'after': (current, c.search, evaluation)}
    probes = {}
    for label, (model, search, service) in variants.items():
        probe = probes[label] = probe_type(monkeypatch)
        for obj, name, stage in (
            (model, 'prepare', 'prepare'), (model, 'available', 'readiness'),
            (model, '_encode' if hasattr(model, '_encode') else 'embed', 'encode'),
            (search, '_ready_snapshot', 'pg.snapshot_guard'), (search, '_metadata', 'pg.eligibility'),
            (service, 'corpus_marker', 'pg.corpus_marker'), (service, 'corpus', 'corpus_guard'),
        ):
            probe.wrap(obj, name, stage)
    commands = {}
    for label, (model, _, service) in variants.items():
        c.indexing.embedding = model
        dataset = service.create_dataset(command, ACCESS)
        commands[label] = {**run_command, 'dataset_id': dataset['dataset_id']}
    timings = {label: defaultdict(list) for label in variants}
    eval_times = defaultdict(list)
    eval_method_times = {label: defaultdict(list) for label in variants}
    records, counts, snapshots = {}, {}, {}
    try:
        for repetition in range(3):
            order = ('before', 'after') if repetition % 2 == 0 else ('after', 'before')
            for query_index, query in enumerate(queries):
                for method in ('bow', 'vector', 'hybrid'):
                    for label in order:
                        model, search, _ = variants[label]
                        c.indexing.embedding = model
                        started = perf_counter()
                        result = search.search(query, method, size=5)
                        timings[label][method].append((perf_counter() - started) * 1000)
                        key = (query_index, method)
                        if key in records:
                            assert observed(result) == records[key], (label, key)
                        records[key] = observed(result)
            for label in order:
                model, _, service = variants[label]
                c.indexing.embedding = model
                probes[label].reset()
                started = perf_counter()
                run = service.run(commands[label], ACCESS)
                eval_times[label].append((perf_counter() - started) * 1000)
                assert run['status'] == 'COMPLETED' and len(run['observations']) == 54
                for row in run['observations']:
                    eval_method_times[label][row['method']].append(row['latency_ms'])
                rows = [{key: row[key] for key in ('query_id', 'method', 'ranked', 'metrics', 'error_code')}
                        for row in run['observations']]
                snapshots[label] = {k: v for k, v in run['snapshot'].items() if k != 'search_version'}
                if 'evaluation' in records:
                    assert rows == records['evaluation'], label
                records['evaluation'] = rows
                counts[label] = probes[label].snapshot()
        assert snapshots['before'] == snapshots['after']
        assert counts['before']['encode']['n'] == 36
        assert counts['after']['encode']['n'] == 18
        for stage in ('pg.corpus_marker', 'pg.snapshot_guard', 'pg.eligibility', 'corpus_guard'):
            assert counts['before'][stage]['n'] == counts['after'][stage]['n'], stage
        return {'baseline_commit': BASE, 'baseline_code_sha256': hashes,
            'search_warm': {label: {method: summarize(values) for method, values in methods.items()}
                            for label, methods in timings.items()},
            'evaluation_total': {label: summarize(values) for label, values in eval_times.items()},
            'evaluation_per_method': {label: {method: summarize(values) for method, values in methods.items()}
                                      for label, methods in eval_method_times.items()},
            'last_evaluation_stages': counts, 'same_fixture_snapshot_hash': checksum(snapshots['before']),
            'identical_order_scores_metrics': True, 'tolerance': 0,
            'note': 'alternating same-fixture same-weights runs; n=3 batches; inclusive stages; no hard thresholds; cold measured separately'}
    finally:
        c.indexing.embedding = current
