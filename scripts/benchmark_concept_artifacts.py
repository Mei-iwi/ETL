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
from data_sync_etl.domain.concepts import empty_dictionary, normalize
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


def one(root, count, *, incremental=False, delta_mode='localized'):
    store = ArtifactStore(root, create=True)
    seed = empty_dictionary()
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
    delta = None
    if incremental:
        def changed():
            remaining = max(1, count // 100)
            for item in records(count):
                bucket = int(sha256(item['concept_id'].encode()).hexdigest()[:8], 16) % store.shards
                if remaining and (bucket == 0 or delta_mode != 'localized'):
                    item['definition'] = 'Synthetic localized one-percent delta.'
                    remaining -= 1
                    if delta_mode == 'revoke':
                        continue
                yield item

        delta = {'changed_fraction': .01, 'distribution': delta_mode,
            'warning': 'Random changes spread across every shard can require a full rebuild.'}
        for reuse in (False, True):
            start, cpu_start = perf_counter(), process_time()
            updated = store.build(changed(), seed_hash=seed.fingerprint, source_token=ScaleAuthority().token(),
                ledger_hash='synthetic-approval-fixture', expected_parent=manifest['generation'], fence=lambda: True,
                reuse_shards=reuse)
            delta['reuse' if reuse else 'full_rebuild'] = {'seconds': perf_counter() - start,
                'cpu_seconds': process_time() - cpu_start, 'generation': updated['generation'],
                'counters': dict(store.last_build_stats)}
        assert delta['reuse']['generation'] == delta['full_rebuild']['generation']
    for provider in providers.values():
        provider.close()
    return {'scope': 'offline_synthetic', 'concepts': count, 'term_keys': 2 * count,
        'artifact_implementation_sha256': sha256((Path(__file__).resolve().parents[1] /
            'src/data_sync_etl/adapters/concept_artifacts.py').read_bytes()).hexdigest(),
        'build_seconds': build_seconds, 'concepts_per_second': count / build_seconds,
        'cold_verify_seconds': load_seconds, 'shard_disk_bytes': sum(row['bytes'] for row in manifest['shards']),
        'cpu_seconds': process_time() - cpu, 'peak_rss_bytes': peak_rss(),
        'disk_lookup_uncached_ms': quantiles(uncached), 'runtime': measurements,
        'incremental_delta': delta,
        'python': platform.python_version(), 'platform': platform.system(), 'logical_cpus': os.cpu_count(),
        'independent_review_or_production_acceptance': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--sizes', nargs='+', type=int, default=[10000, 100000])
    parser.add_argument('--one-size', type=int)
    parser.add_argument('--incremental', action='store_true', help='Compare full/reused localized 1% delta offline')
    parser.add_argument('--allow-million', action='store_true', help='Explicit separate resource approval for >100k synthetic records')
    parser.add_argument('--delta-mode', choices=['localized', 'distributed', 'revoke'], default='localized')
    args = parser.parse_args()
    if not args.root.is_absolute():
        parser.error('Explicit absolute OFFLINE output root required')
    sizes = [args.one_size] if args.one_size is not None else args.sizes
    if any(not 1 <= size <= (1000000 if args.allow_million else 100000) for size in sizes):
        parser.error('Default cap is 100k; >100k requires separate --allow-million opt-in and resource review')
    if args.one_size is not None:
        if not 1 <= args.one_size <= 1000000:
            parser.error('Size exceeds synthetic benchmark limit')
        print(json.dumps(one(args.root, args.one_size, incremental=args.incremental, delta_mode=args.delta_mode)))
        return
    if args.root.exists():
        parser.error('Use a fresh benchmark root; never overwrite old evidence')
    args.root.mkdir(parents=True)
    outputs = []
    for size in args.sizes:
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--root', str(args.root / str(size)),
            '--one-size', str(size), *(['--incremental'] if args.incremental else []),
            '--delta-mode', args.delta_mode,
            *(['--allow-million'] if args.allow_million else [])],
            capture_output=True, text=True, check=True, timeout=300)
        outputs.append(json.loads(completed.stdout))
        (args.root / 'results.json').write_text(json.dumps(outputs, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(outputs, indent=2))


if __name__ == '__main__':
    main()
