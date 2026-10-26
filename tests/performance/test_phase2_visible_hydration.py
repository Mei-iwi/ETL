"""Opt-in paired service benchmark: SQLite/memory fixture, not live HTTP/model."""
import hashlib
import importlib.util
import json
import os
import random
import sys
from copy import deepcopy
from pathlib import Path
from time import perf_counter

import pytest

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.evaluation import percentile
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_search import searchable as searchable


@pytest.mark.skipif(os.environ.get('ETL_PHASE2_BENCHMARK') != '1', reason='Explicit Phase2 isolated profile required')
def test_paired_visible_hydration_profile(searchable):
    c, first = searchable
    root = Path('.test-tmp/search-hybrid-phase2')
    output = Path(os.environ.get('ETL_PHASE2_PROFILE_OUTPUT', str(root / 'hydration-profile.json')))
    if output.exists() or not output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        pytest.fail('New private evidence output required')
    baseline_file = root / 'phase1-source/src/data_sync_etl/application/search.py'
    freeze = json.loads((root / 'phase1-freeze.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(baseline_file.read_bytes()).hexdigest() == freeze['files']['src/data_sync_etl/application/search.py']
    spec = importlib.util.spec_from_file_location('phase2_service_baseline', baseline_file)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    with c.uow() as repo:
        template = repo.get('master_learning_resources', first['resource_id'])
    for number in range(1, 6):
        resource_id = f'phase2-fixture-{number}'
        with c.uow() as repo:
            repo.insert('master_learning_resources', {**template, 'id': resource_id, 'mongo_id': None,
                                                     'source_origin': 'LOCAL'})
        version = c.ingestion.run(resource_id, 'input', 'sample.pdf')['version']
        c.ocr.create_job(version['id'])
        while c.worker.once('phase2-perf-fixture'):
            pass
        c.indexing.enqueue(version['id'])
        assert c.indexing.once()
    services = {'before': baseline.SearchService(c.indexing, c.settings, c.indexing.bags),
                'after': SearchService(c.indexing, c.settings, c.indexing.bags)}
    stats = {name: {'calls': 0, 'hydration_ms': 0.} for name in services}
    for name, service in services.items():
        original = service._lexical_evidence
        def measured(*args, original=original, name=name):
            start = perf_counter()
            try:
                return original(*args)
            finally:
                stats[name]['calls'] += 1
                stats[name]['hydration_ms'] += (perf_counter() - start) * 1000
        service._lexical_evidence = measured
    access = SearchAccess('local_demo')
    for service in services.values():
        service.search('page text', 'bow', size=1, access=access)
    stats = {name: {'calls': 0, 'hydration_ms': 0.} for name in services}
    samples, parity, rng = [], True, random.Random(20261024)
    for repeat in range(10):
        for method in ('bow', 'vector', 'hybrid'):
            for page, size in ((1, 1), (2, 1), (1, 5)):
                arms = list(services)
                rng.shuffle(arms)
                outputs = []
                for name in arms:
                    start = perf_counter()
                    result = services[name].search('page text', method, page=page, size=size, access=access)
                    elapsed = (perf_counter() - start) * 1000
                    payload = deepcopy(result)
                    # New explicit experimental settings change this cache key
                    # intentionally, not ranking, evidence or access behavior.
                    payload.pop('search_fingerprint', None)
                    outputs.append(payload)
                    samples.append({'arm': name, 'method': method, 'page': page, 'size': size,
                                    'repeat': repeat, 'ms': elapsed})
                parity &= outputs[0] == outputs[1]
    report = {'scope': 'isolated SQLite/memory service, fake 3D encoder; not HTTP or real BGE latency',
        'source_baseline_sha256': freeze['files']['src/data_sync_etl/application/search.py'],
        'resources': 6, 'output_parity_excluding_intentional_policy_fingerprint': parity,
        'request_counts': {name: len([r for r in samples if r['arm'] == name]) for name in services},
        'evidence': stats, 'summary': {name: {method: {
            'p50_ms': percentile([r['ms'] for r in samples if r['arm'] == name and r['method'] == method], .5),
            'p95_ms': percentile([r['ms'] for r in samples if r['arm'] == name and r['method'] == method], .95),
            'samples': sum(r['arm'] == name and r['method'] == method for r in samples)}
            for method in ('bow', 'vector', 'hybrid')} for name in services}, 'samples': samples}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    assert parity
    assert stats['before']['calls'] == 6 * 90
    assert stats['after']['calls'] == 7 * 30
