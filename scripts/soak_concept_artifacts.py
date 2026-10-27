"""Bounded 30-120 s OFFLINE synthetic publish/restart/revoke/GC exercise only."""
import argparse
import json
from pathlib import Path
from time import monotonic, sleep

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.application.concept_resolver import ConceptDictionaryProvider
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.corpus_concepts import EligibilityFence

if __package__:
    from scripts.benchmark_concept_artifacts import peak_rss, quantiles, records
else:
    from benchmark_concept_artifacts import peak_rss, quantiles, records


class Authority:
    completeness_verified = True
    scope = 'offline_fixture'
    revision = 0

    def token(self):
        return f'synthetic-authority-{self.revision}'


def run(root, seconds):
    if not root.is_absolute() or root.exists() or not 30 <= seconds <= 120:
        raise ValueError('Fresh absolute root and explicit 30..120 seconds required')
    store = ArtifactStore(root, create=True, max_bytes=4 * 1024 * 1024)
    authority, seed = Authority(), default_dictionary()
    provider = ConceptDictionaryProvider(seed, store=store, enabled=True, allow_offline=True,
        fence=EligibilityFence(authority), lookup_ms=100)
    started, samples = monotonic(), []
    totals = {'iterations': 0, 'revocations': 0, 'injected_crashes': 0, 'deleted_generations': 0}
    try:
        while monotonic() - started < seconds:
            if (peak_rss() or 0) > 128 * 1024 * 1024 or totals['iterations'] >= 1000:
                raise RuntimeError('Synthetic soak resource cap exceeded')
            authority.revision += 1
            with provider.pin('synthetic concept 000000') as dictionary:
                assert dictionary is seed  # Revocation never waits for rebuild/GC.
            totals['revocations'] += 1
            # A separate local store instance simulates scheduler process restart.
            publisher = ArtifactStore(root, max_bytes=4 * 1024 * 1024)
            manifest = publisher.build(records(32), seed_hash=seed.fingerprint, source_token=authority.token(),
                ledger_hash='synthetic-review-only', expected_parent=store.current(), fence=lambda: True)
            if totals['iterations'] % 10 == 0:
                previous, write = publisher.current(), publisher.write_json

                def interrupted(relative, value, _write=write):
                    if relative == 'current.json':
                        raise OSError('synthetic pointer interruption')
                    return _write(relative, value)

                publisher.write_json = interrupted
                try:
                    publisher.activate(manifest['generation'], fence=lambda _: True)
                except OSError:
                    assert publisher.current() == previous
                    totals['injected_crashes'] += 1
                finally:
                    publisher.write_json = write
            publisher.activate(manifest['generation'], fence=lambda item: item['source_token'] == authority.token())
            assert provider.refresh()['status'] == 'OFFLINE_READY'
            before = monotonic()
            with provider.pin('synthetic concept 000000') as dictionary:
                assert dictionary is not seed
                assert dictionary.expand('synthetic concept 000000').terms
                store.gc(retain=2, min_age_seconds=0, grace_seconds=0, dry_run=False)
            samples.append((monotonic() - before) * 1000)
            totals['deleted_generations'] += len(store.gc(retain=2, min_age_seconds=0,
                grace_seconds=0, dry_run=False)['deleted'])
            totals['iterations'] += 1
            sleep(.05)
    finally:
        provider.close()
    report = {'scope': 'synthetic_local_host', 'status': 'OFFLINE_VERIFIED', **totals,
        'elapsed_seconds': monotonic() - started, 'peak_rss_bytes': peak_rss(),
        'lookup_plus_gc_ms': quantiles(samples), 'real_power_loss_tested': False,
        'multi_process_tested': False, 'multi_host_tested': False, 'long_soak_tested': False,
        'live_status': 'BLOCKED_LIVE'}
    with (root / 'results.json').open('x', encoding='utf-8') as target:
        json.dump(report, target, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--seconds', required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(run(args.root, args.seconds)))


if __name__ == '__main__':
    main()
