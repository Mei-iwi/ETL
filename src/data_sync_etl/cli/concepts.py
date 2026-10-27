"""Offline fixture operator commands. No Container/FastAPI or database startup.

python -m data_sync_etl.cli.concepts --help
Live discovery/shared publication deliberately remain BLOCKED_LIVE.
"""
import argparse
import json
from dataclasses import fields
from itertools import islice
from pathlib import Path

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.adapters.concept_corpus import FixtureCorpus
from data_sync_etl.application.concept_discovery import ConceptDiscoveryWorker, term_target
from data_sync_etl.config import Settings
from data_sync_etl.domain.corpus_concepts import DiscoveryLimits
from data_sync_etl.ports.concept_corpus import ConceptBlocked, ResourceEvidence


def fixture(path):
    if path.suffix != '.json' or path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Bounded synthetic JSON fixture required')
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('scope') != 'offline_fixture' or len(value.get('resources', [])) > 1000:
        raise ConceptBlocked('SYNTHETIC_FIXTURE_REQUIRED')
    rows = []
    names = {field.name for field in fields(ResourceEvidence)}
    for item in value['resources']:
        if set(item) - names or item.get('scope') != 'offline_fixture':
            raise ConceptBlocked('SYNTHETIC_FIXTURE_REQUIRED')
        item = {**item, 'units': tuple(item.get('units', [])),
                'approved_unit_ids': frozenset(item.get('approved_unit_ids', []))}
        rows.append(ResourceEvidence(**item))
    return FixtureCorpus(rows)


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument('action', choices=['example', 'audit', 'status', 'discover', 'reconcile', 'run', 'candidates',
                                        'review', 'build', 'validate', 'publish', 'rollback', 'run-worker',
                                        'pause', 'resume', 'preflight', 'recover', 'gc'])
    root.add_argument('--root', type=Path, required=True, help='Explicit private OFFLINE fixture root only')
    root.add_argument('--fixture', type=Path)
    root.add_argument('--generation')
    root.add_argument('--candidate-id')
    root.add_argument('--binding', help='Select one evidence binding when the same relation has multiple versions')
    root.add_argument('--kind', choices=['term', 'relation'])
    root.add_argument('--term-index', type=int, choices=[0, 1])
    root.add_argument('--decision', choices=['approved', 'rejected'])
    root.add_argument('--reviewer')
    root.add_argument('--loop', action='store_true', help='Standalone worker until Ctrl+C; default one tick')
    root.add_argument('--live', action='store_true', help='Reports BLOCKED_LIVE; never opens DB connections')
    root.add_argument('--apply', action='store_true', help='Explicit OFFLINE worker/GC execution; default dry-run')
    root.add_argument('--retain', type=int, default=3, help='GC minimum releases retained (1..100)')
    return root


def execute(args):
    if args.live:
        raise ConceptBlocked('BLOCKED_LIVE_RO_REVOCATION_ARTIFACT_NOT_VERIFIED')
    if not args.root.is_absolute():
        raise ValueError('Absolute offline root required')
    settings = Settings(_env_file=None)
    # Do not initialize the application, secrets, models or database to run CLI.
    store = ArtifactStore(args.root, create=True, shards=settings.concept_artifact_shards,
                          max_bytes=settings.concept_artifact_max_mb * 1024 * 1024)
    marker = store.read_json('offline-fixture.json')
    if marker is None:
        if any(item.name != 'offline-fixture.json' for item in args.root.iterdir()):
            raise ConceptBlocked('OFFLINE_ROOT_NOT_EMPTY')
        store.write_json('offline-fixture.json', {'scope': 'offline_fixture'})
    elif marker != {'scope': 'offline_fixture'}:
        raise ConceptBlocked('OFFLINE_ROOT_REQUIRED')
    if args.action == 'preflight':
        return store.preflight()
    if args.action == 'recover':
        return store.recovery_status()
    if args.action == 'gc':
        return store.gc(retain=args.retain, dry_run=not args.apply)
    if args.action in ('pause', 'resume'):
        with store.writer_lock():
            state = store.read_json('checkpoints/state.json')
            if state is None:
                raise ConceptBlocked('WORKER_NOT_INITIALIZED')
            state['paused'] = args.action == 'pause'
            if args.action == 'resume':
                state.update(next_due=0., failures=0)
            store.write_json('checkpoints/state.json', state)
        return state
    if args.action == 'example':
        target = store.path('example-corpus.json')
        if target.exists():
            raise ValueError('Example already exists; refusing overwrite')
        store.write_json('example-corpus.json', {'scope': 'offline_fixture', 'resources': [
            {'resource_id': f'fixture-r-{index:03}', 'version_id': f'fixture-v-{index:03}', 'revision': 1,
             'domain': 'computing', 'fields': {'title': 'Mémoire tampon (cache)' if index % 2 else 'Bộ nhớ đệm (cache)'},
             'public': index < 30, 'metadata_approved': True, 'scope': 'offline_fixture', 'source_updated_at': 0.}
            for index in range(35)]})
        return {'scope': 'offline_fixture', 'resources': 35, 'fixture': str(target)}
    if args.action in ('audit', 'status'):
        return {'status': 'OFFLINE_TEST_ONLY', 'generation': store.current(),
            'worker': store.read_json('checkpoints/state.json'), 'live_ro_verified': False,
            'revocation_completeness_verified': False, 'artifact_root_live_verified': False}
    if args.action == 'validate':
        release = store.load(args.generation)
        return {'status': 'VERIFIED_OFFLINE', 'generation': release.manifest['generation'], 'count': release.manifest['count']}
    if args.fixture is None:
        raise ValueError('--fixture is required; live authority is unavailable')
    inventory = fixture(args.fixture)
    worker = ConceptDiscoveryWorker(inventory, store, batch_size=settings.concept_builder_batch_size,
        tick_seconds=settings.concept_builder_tick_seconds,
        limits=DiscoveryLimits(max_chars=settings.concept_builder_max_chars, max_candidates=settings.concept_builder_max_candidates))
    if args.action == 'run-worker':
        if not args.apply:
            return {'status': 'DRY_RUN', 'scope': 'offline_fixture', 'live_status': 'BLOCKED_LIVE'}
        from data_sync_etl.application.concept_scheduler import ConceptWorkerScheduler
        from data_sync_etl.workers.lifecycle import worker_lifecycle
        scheduler = ConceptWorkerScheduler(worker, lambda: fixture(args.fixture))
        with store.worker_lock(), worker_lifecycle() as stop:
            result = scheduler.step()
            while args.loop and not stop.wait(1.):
                result = scheduler.step()
        return result
    if args.action == 'reconcile':
        worker.reconcile()
        return worker.tick()
    if args.action in ('discover', 'run'):
        result = worker.tick()
        if args.loop:
            from data_sync_etl.workers.lifecycle import worker_lifecycle
            with store.worker_lock(), worker_lifecycle() as stop:
                while not stop.is_set():
                    # Fixture file is an offline source, not a permanently frozen
                    # cache. Reload bounded input between ticks; NEVER use old
                    # authority to process while its source is unreadable.
                    try:
                        inventory = fixture(args.fixture)
                    except (OSError, ValueError, KeyError, TypeError):
                        stop.wait(5.)
                        continue
                    worker = ConceptDiscoveryWorker(inventory, store, batch_size=settings.concept_builder_batch_size,
                        tick_seconds=settings.concept_builder_tick_seconds,
                        limits=DiscoveryLimits(max_chars=settings.concept_builder_max_chars,
                                               max_candidates=settings.concept_builder_max_candidates))
                    result = worker.tick()
                    stop.wait(1.)
        return result
    if args.action == 'candidates':
        return {'scope': 'offline_fixture', 'relations': list(islice((
            {'id': item['id'], 'binding': item['binding'], 'kind': item['kind'], 'terms': item['terms']}
            for record in worker.contributions() for item in record['relations']), 100))}
    if args.action == 'review':
        if not all((args.candidate_id, args.kind, args.decision, args.reviewer)):
            raise ValueError('Candidate, kind, decision and reviewer are required')
        for record in worker.contributions():
            for candidate in record['relations']:
                if candidate['id'] == args.candidate_id and (args.binding is None or candidate['binding'] == args.binding):
                    if args.kind == 'term' and args.term_index is None:
                        raise ValueError('--term-index required')
                    target = candidate['id'] if args.kind == 'relation' else term_target(candidate['terms'][args.term_index], candidate)
                    return {'status': 'REVIEW_RECORDED_OFFLINE', 'hash': worker.ledger.decide(candidate,
                        kind=args.kind, target=target, decision=args.decision, reviewer=args.reviewer)}
        raise ValueError('Candidate not found')
    if args.action == 'build':
        return worker.build()
    if not args.generation:
        raise ValueError('--generation required')
    if args.action == 'publish':
        store.activate(args.generation, fence=lambda manifest: worker.fence.unchanged(manifest['source_token'])
            and worker.ledger.snapshot()[0] == manifest['ledger_hash'])
    elif args.action == 'rollback':
        store.rollback(args.generation, fence=worker.fence.unchanged)
    return {'status': 'ACTIVATED_OFFLINE_ONLY', 'generation': store.current()}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        print(json.dumps(execute(args), ensure_ascii=True, default=str))
        return 0
    except (ConceptBlocked, ValueError, OSError, KeyError, TypeError):
        print(json.dumps({'status': 'BLOCKED_LIVE' if args.live else 'BLOCKED_OFFLINE',
                          'code': 'CONCEPT_OPERATOR_PRECONDITION_FAILED'}))
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
