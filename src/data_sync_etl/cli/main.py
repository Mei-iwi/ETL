import argparse
import json
import sys

import pymupdf

from data_sync_etl.composition import Container
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
    commands.add_parser("recover-stale-ocr-tasks")
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
    match args.command:
        case "sync-master":
            return container.sync.run(args.stream, args.full)
        case "ingest-resource":
            return container.ingestion.run(
                args.resource_id, args.bucket, args.object_key, args.uploaded_by
            )
        case "create-ocr-job":
            return container.ocr.create_job(args.resource_version_id)
        case "run-ocr-worker":
            return container.worker.run(args.worker_id, args.once)
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
    except Exception:
        print(
            "ETL operation failed. Check configuration and stage status; sensitive details omitted.",
            file=sys.stderr,
        )
        return 1
    finally:
        if container:
            container.engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
