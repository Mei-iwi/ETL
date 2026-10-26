"""Reproducible OFFLINE synthetic artifact scale test; no DB/model/network.

Each scale runs in a fresh process. SQLite is immutable build output; synthetic
approval flags are fixture input, never independent review/quality acceptance.
"""
import argparse
import ctypes
import json
import os
import platform
import statistics
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from time import perf_counter, process_time

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.application.concept_resolver import ConceptDictionaryProvider
from data_sync_etl.domain.concepts import default_dictionary, normalize
from data_sync_etl.domain.corpus_concepts import EligibilityFence


def peak_rss():
    if os.name == 'nt':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD), *[
                (name, ctypes.c_size_t) for name in ('peak_ws', 'ws', 'peak_paged', 'paged', 'peak_nonpaged',
                                                   'nonpaged', 'pagefile', 'peak_pagefile')]]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        get_process = ctypes.windll.kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
        get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        get_memory.restype = wintypes.BOOL
        if get_memory(get_process(), ctypes.byref(counters), counters.cb):
            return counters.peak_ws
        return None
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def records(count):
    source = [{'name': 'synthetic scale fixture only', 'identifier': 'not-independent-human-approval'}]
    for index in range(count):
        labels = {'en': f'synthetic concept {index:06}', 'vi': f'thuật ngữ mẫu {index:06}'}
        cid = f'corpus:scale-{index:06}'
        yield {'concept_id': cid, 'preferred_labels': labels, 'definition': 'Synthetic benchmark fixture.',
            'domain': 'synthetic', 'terms': [{'text': text, 'normalized': normalize(text), 'language': lang,
                'kind': 'preferred', 'concept_id': cid, 'domain': 'synthetic', 'sources': source,
                'confidence': 0.9, 'review_status': 'approved'} for lang, text in labels.items()],
            'sources': source, 'review_status': 'approved', 'version': 'fixture-v1'}


def quantiles(values):
    ordered = sorted(values)
    return {'p50': statistics.median(values), 'p95': ordered[int(.95 * (len(values) - 1))],
            'p99': ordered[int(.99 * (len(values) - 1))], 'samples': len(values)}


class ScaleAuthority:
    scope = 'offline_fixture'
    completeness_verified = True
    def token(self):
        return 'synthetic-frozen-authority'


def one(root, count):
    store = ArtifactStore(root, create=True)
    seed = default_dictionary()
    cpu = process_time()
    start = perf_counter()
    manifest = store.build(records(count), seed_hash=seed.fingerprint, source_token=ScaleAuthority().token(),
        ledger_hash='synthetic-approval-fixture', fence=lambda: True, activate=True)
    build_seconds = perf_counter() - start
    start = perf_counter()
    release = store.load()
    load_seconds = perf_counter() - start
    queries = [f'synthetic concept {index:06}' for index in range(min(8, count))]
    fence = EligibilityFence(ScaleAuthority())
    providers = {
        'seed_only': ConceptDictionaryProvider(seed),
        'dynamic_disabled': ConceptDictionaryProvider(seed, store=store, enabled=False),
        'dynamic_fixture_enabled': ConceptDictionaryProvider(seed, store=store, enabled=True, fence=fence,
            allow_offline=True, lookup_ms=100),
    }
    providers['dynamic_fixture_enabled'].refresh()
    measurements = {}
    for name, provider in providers.items():
        for query in queries:
            with provider.pin(query) as dictionary:
                dictionary.expand(query)
        samples = []
        for index in range(1000):
            query = queries[index % len(queries)]
            start = perf_counter()
            with provider.pin(query) as dictionary:
                dictionary.expand(query)
            samples.append((perf_counter() - start) * 1000)
        measurements[name] = {'warm_resolve_and_expand_ms': quantiles(samples), 'cache': provider.status()}
    uncached = []
    for index in range(100):
        start = perf_counter()
        assert release.lookup([f'synthetic concept {index % count:06}'], budget_ms=100)
        uncached.append((perf_counter() - start) * 1000)
    return {'scope': 'offline_synthetic', 'concepts': count, 'term_keys': 2 * count,
        'artifact_implementation_sha256': sha256((Path(__file__).resolve().parents[1] /
            'src/data_sync_etl/adapters/concept_artifacts.py').read_bytes()).hexdigest(),
        'build_seconds': build_seconds, 'concepts_per_second': count / build_seconds,
        'cold_verify_seconds': load_seconds, 'shard_disk_bytes': sum(row['bytes'] for row in manifest['shards']),
        'cpu_seconds': process_time() - cpu, 'peak_rss_bytes': peak_rss(),
        'disk_lookup_uncached_ms': quantiles(uncached), 'runtime': measurements,
        'python': platform.python_version(), 'platform': platform.system(), 'logical_cpus': os.cpu_count(),
        'independent_review_or_production_acceptance': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--sizes', nargs='+', type=int, default=[10000, 100000])
    parser.add_argument('--one-size', type=int)
    args = parser.parse_args()
    if not args.root.is_absolute():
        parser.error('Explicit absolute OFFLINE output root required')
    if args.one_size is not None:
        if not 1 <= args.one_size <= 1000000:
            parser.error('Size exceeds synthetic benchmark limit')
        print(json.dumps(one(args.root, args.one_size)))
        return
    if args.root.exists():
        parser.error('Use a fresh benchmark root; never overwrite old evidence')
    args.root.mkdir(parents=True)
    outputs = []
    for size in args.sizes:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--root', str(args.root / str(size)),
            '--one-size', str(size)], capture_output=True, text=True, check=True)
        outputs.append(json.loads(completed.stdout))
        (args.root / 'results.json').write_text(json.dumps(outputs, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(outputs, indent=2))


if __name__ == '__main__':
    main()
