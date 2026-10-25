import argparse
import json
import sys

import pymupdf

from data_sync_etl.composition import Container
from data_sync_etl.domain.core import DomainError
from data_sync_etl.logging import configure, flow


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "Invalid CLI arguments; run --help for usage.\n")


def parser():
    root = SafeArgumentParser(description="Standalone Data Sync + ETL")
    commands = root.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("sync-master")
    sync.add_argument(
        "--stream", choices=["all", "resources", "users", "grades", "subjects"], default="all"
    )
    mode = sync.add_mutually_exclusive_group(required=True)
    mode.add_argument("--full", action="store_true")
    mode.add_argument("--incremental", action="store_true")
    reconcile = commands.add_parser('reconcile-master', help='Read-only preview by default; apply needs approved fingerprint')
    reconcile.add_argument('--stream', choices=['all', 'grades', 'subjects', 'users', 'resources'], default='all')
    action = reconcile.add_mutually_exclusive_group()
    action.add_argument('--dry-run', action='store_true')
    action.add_argument('--apply', action='store_true')
    reconcile.add_argument('--fingerprint')
    project = commands.add_parser('project-master-mongo')
    project.add_argument('--stream', choices=['all', 'users', 'resources'], default='all')
    project.add_argument('--full', action='store_true', required=True,
                         help='Full scan; shared validates by default, identity projection needs explicit opt-in')
    project_action = project.add_mutually_exclusive_group()
    project_action.add_argument('--dry-run', action='store_true', help='Read-only preview (default)')
    project_action.add_argument('--apply', action='store_true', help='Explicitly apply configured Dev projection')
    metadata = commands.add_parser('sync-mongo-metadata', help='Shared metadata read-only check; separate source copy only when explicitly configured')
    metadata.add_argument('--stream', choices=['all', 'users', 'resources'], default='all')
    metadata.add_argument('--full', action='store_true', required=True)
    metadata_action = metadata.add_mutually_exclusive_group()
    metadata_action.add_argument('--dry-run', action='store_true', help='Read-only preview (default)')
    metadata_action.add_argument('--apply', action='store_true', help='Apply separate-source copy in Dev only')
    ingest = commands.add_parser("ingest-resource")
    ingest.add_argument("--resource-id", required=True)
    ingest.add_argument("--bucket", required=True)
    ingest.add_argument("--object-key", required=True)
    ingest.add_argument("--uploaded-by")
    for name in ["create-ocr-job", "postprocess", "etl-status", "resume-etl"]:
        cmd = commands.add_parser(name)
        cmd.add_argument("--resource-version-id", required=True)
    worker = commands.add_parser("run-ocr-worker")
    worker.add_argument("--worker-id", required=True)
    worker.add_argument("--once", action="store_true")
    sync_worker = commands.add_parser("run-master-sync-worker")
    sync_worker.add_argument("--worker-id", required=True)
    sync_worker.add_argument("--once", action="store_true")
    file_worker = commands.add_parser("run-source-file-worker")
    file_worker.add_argument("--worker-id", required=True)
    file_worker.add_argument("--once", action="store_true")
    commands.add_parser("source-file-precheck")
    commands.add_parser("recover-stale-ocr-tasks")
    semantic_schema = commands.add_parser('semantic-schema', help='Check additive chunk index; no legacy schema changes')
    semantic_schema.add_argument('--apply', action='store_true', help='Explicit additive installation only')
    semantic_status = commands.add_parser('semantic-status', help='Read-only chunk coverage and index status')
    semantic_status.add_argument('--resource-version-id', required=True)
    semantic = commands.add_parser('semantic-index', help='One CONTENT_READY version; dry-run by default, repeat to resume')
    selection = semantic.add_mutually_exclusive_group(required=True)
    selection.add_argument('--resource-version-id', action='append')
    selection.add_argument('--full', action='store_true', help='Bounded published-version batch, never automatic')
    semantic.add_argument('--limit', type=int, default=20)
    semantic.add_argument('--after-resource-id', default='')
    semantic.add_argument('--apply', action='store_true')
    schema = commands.add_parser('metadata-search-schema', help='Check additive metadata index; no startup writes')
    schema.add_argument('--apply', action='store_true')
    schema.add_argument('--candidates', action='store_true', help='Check/install opt-in surface candidate index')
    commands.add_parser('metadata-search-diagnostics', help='Private operator storage counts/checkpoint; no resource IDs or writes')
    metadata_index = commands.add_parser('metadata-search-index', help='Bounded metadata reconcile; dry-run by default')
    metadata_index.add_argument('--resource-id')
    metadata_index.add_argument('--apply', action='store_true')
    metadata_index.add_argument('--dense', action='store_true')
    prune = commands.add_parser('metadata-search-prune', help='Bounded orphan tombstones; dry-run by default')
    prune.add_argument('--after-version-id', default='')
    prune.add_argument('--limit', type=int, default=100)
    prune.add_argument('--apply', action='store_true')
    demo = commands.add_parser("demo-run")
    demo.add_argument("--fixture-resource-id", default="resource-1")
    return root


def demo_run(container, resource_id):
    # Demo-only bootstrap; regular ingestion never performs an implicit master sync.
    with container.uow() as repo:
        resource = repo.get("master_learning_resources", resource_id)
    if resource is None:
        container.sync.run(full=True)
    path = container.storage.get_local_path("demo", "sample.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with pymupdf.open() as pdf:
            for number in range(1, 4):
                page = pdf.new_page()
                page.insert_text(
                    (72, 72),
                    f"Demo learning material - page {number}\nNative PDF text for ETL testing.",
                )
            pdf.save(path)
    result = container.orchestrator.start(resource_id, "demo", "sample.pdf")
    version_id = result["version"]["id"]
    if result["action"] == "NO_CHANGE":
        return {**result, "status": container.orchestrator.status(version_id)}
    # Demo CLI only. Production workers run in separate processes.
    while container.worker.once("demo-worker"):
        pass
    container.postprocess.run(version_id)
    return container.orchestrator.status(version_id)


def dispatch(container, args):
    if args.command in ('metadata-search-schema', 'metadata-search-index', 'metadata-search-prune', 'metadata-search-diagnostics'):
        if container.metadata_search is None:
            raise DomainError('METADATA_SCHEMA_NOT_READY')
        if args.command == 'metadata-search-diagnostics':
            return container.metadata_search.diagnostics()
        if args.apply:
            import re

            from sqlalchemy.engine import make_url

            db_name = make_url(container.settings.database_url).database or ''
            mongo_name = container.metadata_search.store.database.name
            def isolated(name):
                return bool(re.search(r'(^|[_-])test([_-]|$)', name, re.I))
            if (container.settings.app_environment not in ('development', 'test')
                    or not isolated(db_name) or not isolated(mongo_name)):
                raise DomainError('METADATA_APPLY_REQUIRES_ISOLATED_TEST_DATABASES')
        if args.command == 'metadata-search-schema':
            if args.apply:
                from data_sync_etl.adapters.mongo.metadata_search import install
                install(container.metadata_search.store.database, candidates=args.candidates)
            else:
                container.metadata_search.store.ensure_ready()
            if args.candidates:
                container.metadata_search.store.ensure_candidates_ready()
            return {'status': 'READY'}
        if args.command == 'metadata-search-prune':
            return container.metadata_search.prune(after=args.after_version_id, limit=args.limit, apply=args.apply)
        return container.metadata_search.reconcile(
            {'resource_id': args.resource_id} if args.resource_id else {}, apply=args.apply, dense=args.dense)
    if args.command in ('semantic-schema', 'semantic-index', 'semantic-status'):
        if container.semantic is None:
            raise DomainError('SEMANTIC_SCHEMA_NOT_READY')
        if args.command == 'semantic-schema':
            if args.apply:
                from data_sync_etl.adapters.mongo.semantic import install
                install(container.semantic.store.database)
            else:
                container.semantic.store.ensure_ready()
            return {'status': 'PASS', 'operation': 'semantic-schema'}
        if args.command == 'semantic-status':
            return container.semantic.status(args.resource_version_id)
        if not 1 <= args.limit <= 100:
            raise DomainError('SEMANTIC_BATCH_LIMIT')
        versions, next_cursor = args.resource_version_id or [], None
        if args.full:
            with container.uow() as repo:
                rows = repo.search_versions({}, limit=container.settings.search_max_bags + 1)
            if len(rows) > container.settings.search_max_bags:
                raise DomainError('SEMANTIC_CANDIDATE_LIMIT')
            rows = sorted((row for row in rows if row['resource_id'] > args.after_resource_id),
                          key=lambda row: row['resource_id'])
            selected = rows[:args.limit]
            versions = [row['version_id'] for row in selected]
            if len(rows) > len(selected):
                next_cursor = selected[-1]['resource_id']
        if len(versions) > args.limit or len(set(versions)) != len(versions):
            raise DomainError('SEMANTIC_BATCH_LIMIT')
        results = []
        for version_id in versions:
            try:
                results.append({'resource_version_id': version_id,
                    **container.semantic.build(version_id, apply=args.apply)})
            except Exception as exc:
                results.append({'resource_version_id': version_id, 'status': 'FAILED',
                    'code': (getattr(exc, 'code', 'SEMANTIC_INDEX_FAILED')
                             if isinstance(exc, DomainError) else 'SEMANTIC_STORAGE_UNAVAILABLE')})
        return {'status': 'PARTIAL' if any(row['status'] == 'FAILED' for row in results) else 'PASS',
                'items': results, 'next_after_resource_id': next_cursor}
    if (args.command in ('project-master-mongo', 'sync-mongo-metadata') and args.apply
            and container.settings.app_environment not in ('development', 'test')):
        raise DomainError('MONGO_METADATA_APPLY_REQUIRES_DEV')
    match args.command:
        case "sync-master":
            return container.sync.run(args.stream, args.full)
        case 'reconcile-master':
            return container.reconcile_master.run(args.stream, apply=args.apply, fingerprint=args.fingerprint)
        case 'project-master-mongo':
            return container.master_metadata_projection.run(args.stream, dry_run=not args.apply)
        case 'sync-mongo-metadata':
            return container.mongo_metadata_sync.run(args.stream, dry_run=not args.apply)
        case "ingest-resource":
            return container.ingestion.run(
                args.resource_id, args.bucket, args.object_key, args.uploaded_by
            )
        case "create-ocr-job":
            return container.ocr.create_job(args.resource_version_id)
        case "run-ocr-worker":
            return container.worker.run(args.worker_id, args.once)
        case "run-master-sync-worker":
            return container.master_sync_worker.run(args.worker_id, args.once)
        case "run-source-file-worker":
            return container.source_file_worker.run(args.worker_id, args.once)
        case "source-file-precheck":
            return container.source_file_import.precheck(container.mongo_client)
        case "recover-stale-ocr-tasks":
            return container.ocr.recover_stale()
        case "postprocess":
            return container.postprocess.run(args.resource_version_id)
        case "etl-status":
            return container.orchestrator.status(args.resource_version_id)
        case "resume-etl":
            return container.orchestrator.resume(args.resource_version_id)
        case "demo-run":
            return demo_run(container, args.fixture_resource_id)


def main():
    args = parser().parse_args()
    container = None
    try:
        container = Container()
        configure(container.settings.log_level)
        with flow():
            result = dispatch(container, args)
        print(json.dumps(result, default=str, ensure_ascii=True, indent=2))
        if args.command == 'metadata-search-index' and result['status'] == 'PARTIAL':
            return 1
        if args.command == 'semantic-index' and result['status'] != 'PASS':
            return 1
        if args.command == 'reconcile-master' and result['status'] == 'BLOCKED':
            return 1
        if args.command in ('project-master-mongo', 'sync-mongo-metadata') and any(
            r['status'] in {'FAILED', 'BLOCKED', 'DISABLED', 'BLOCKED_MONGO_ID_MAPPING', 'BUSY'} for r in result.values()
        ):
            return 1
    except Exception as exc:
        code = str(exc).split(':', 1)[0] if isinstance(exc, DomainError) else None
        if code in {'SHARED_MONGO_CONFLICT_SOURCE_ENABLED', 'SHARED_MONGO_CONFLICT_IDENTITY_BOOTSTRAP'}:
            print(f'{code}: shared mode requires SOURCE_MONGO_ENABLED=false and MASTER_MONGO_IDENTITY_BOOTSTRAP=false.', file=sys.stderr)
        elif code in {'MONGO_IDENTITY_PROJECTION_REQUIRES_SHARED_DEV_POSTGRES', 'MONGO_METADATA_APPLY_REQUIRES_DEV'}:
            print(f'{code}: verify APP_ENVIRONMENT=development/test; identity projection requires shared mode and PostgreSQL SOURCE.', file=sys.stderr)
        else:
            print(
                "ETL operation failed. Check configuration and stage status; sensitive details omitted.",
                file=sys.stderr,
            )
        return 1
    finally:
        if container:
            container.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
