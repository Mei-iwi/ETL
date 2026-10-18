"""Profile HTTP Search read paths only on owned temporary PG/Mongo; no dotenv or Dev URLs."""


def main():
    import argparse
    import os
    import re
    import subprocess
    import sys
    import tempfile
    import time
    import traceback
    import uuid
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--completion", action="store_true", help="Only unfinished medium matrix and concurrency")
    args = parser.parse_args()
    if args.smoke and args.completion:
        parser.error('Choose smoke OR completion')
    model_path = args.model_path.resolve(strict=True)
    if not model_path.is_dir():
        parser.error("Existing local model directory required")
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        parser.error("Run from repository root")
    target = root / "docs/audits/search-read-path" / (
        "baseline_smoke.json" if args.smoke else "baseline_completion.json" if args.completion else "baseline_metrics.json"
    )
    if target.exists():
        parser.error("Refusing to overwrite previous recovery metrics")
    original = root
    sys.path.insert(0, str(root / "src"))
    from data_sync_etl.config import Settings

    Settings.model_config["env_file"] = None
    owner = uuid.uuid4().hex
    containers = []

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Owned Docker operation failed; exit=" + str(result.returncode))
        return result.stdout.strip()

    with tempfile.TemporaryDirectory(prefix="etl-readpath-isolation-") as temp:
        os.environ["DOCKER_CONFIG"] = temp
        try:
            password = uuid.uuid4().hex
            pg = docker(
                "run",
                "--pull",
                "never",
                "-d",
                "--label",
                "etl.readpath.owner=" + owner,
                "--name",
                "etl-readpath-pg-" + owner[:10],
                "--tmpfs",
                "/var/lib/postgresql/data",
                "-e",
                "POSTGRES_USER=etl_test_reader",
                "-e",
                "POSTGRES_DB=etl_test_readpath",
                "-e",
                "POSTGRES_PASSWORD=" + password,
                "-p",
                "127.0.0.1::5432",
                "postgres:16",
            )
            assert re.fullmatch("[0-9a-f]{64}", pg)
            containers.append(pg)
            mongo = docker(
                "run",
                "--pull",
                "never",
                "-d",
                "--label",
                "etl.readpath.owner=" + owner,
                "--name",
                "etl-readpath-mongo-" + owner[:10],
                "--tmpfs",
                "/data/db",
                "--tmpfs",
                "/data/configdb",
                "-p",
                "127.0.0.1::27017",
                "mongo:8.0",
                "--bind_ip_all",
            )
            assert re.fullmatch("[0-9a-f]{64}", mongo)
            containers.append(mongo)

            def port(identifier, inner):
                value = docker("port", identifier, inner)
                assert re.fullmatch(r"127\.0\.0\.1:\d+", value)
                return int(value.rsplit(":", 1)[1])

            pg_port, mongo_port = port(pg, "5432/tcp"), port(mongo, "27017/tcp")
            assert pg_port not in (5432, 5433) and mongo_port not in (27017, 27018)
            import psycopg
            from pymongo import MongoClient

            for attempt in range(40):
                try:
                    with psycopg.connect(
                        host="127.0.0.1",
                        port=pg_port,
                        dbname="etl_test_readpath",
                        user="etl_test_reader",
                        password=password,
                        connect_timeout=1,
                    ):
                        pass
                    with MongoClient(
                        "mongodb://127.0.0.1:" + str(mongo_port), serverSelectionTimeoutMS=500
                    ) as client:
                        client.admin.command("ping")
                    break
                except Exception:
                    if attempt == 39:
                        raise RuntimeError(
                            "Owned test infrastructure did not become ready"
                        ) from None
                    time.sleep(0.5)
            for key in (
                "TEST_SOURCE_DATABASE_URL",
                "ETL_REQUIRE_SOURCE",
                "SOURCE_DATABASE_URL",
                "SOURCE_MIGRATION_DATABASE_URL",
                "SOURCE_MONGO_URI",
                "MONGO_URI",
                "DATABASE_URL",
            ):
                os.environ.pop(key, None)
            os.environ.update(
                TEST_DATABASE_URL="postgresql+psycopg://etl_test_reader:"
                + password
                + "@127.0.0.1:"
                + str(pg_port)
                + "/etl_test_readpath",
                TEST_MONGO_URI="mongodb://127.0.0.1:" + str(mongo_port),
                ETL_REQUIRE_POSTGRES="1",
                ETL_READPATH_PHYSICAL="1",
                ETL_READPATH_HEAD=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                ETL_READPATH_SMOKE="1" if args.smoke else "0",
                ETL_READPATH_COMPLETION="1" if args.completion else "0",
                ETL_QUERY_CACHE_PHYSICAL="1",
                ETL_SEARCH_REAL_MODEL="1",
                ETL_QUERY_CACHE_MODEL_PATH=str(model_path),
                ETL_READPATH_METRICS=str(target),
                INDEX_EMBEDDING_MODEL_PATH=str(model_path),
                INDEX_EMBEDDING_MODEL_ID="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
                INDEX_EMBEDDING_MODEL_VERSION="e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
                INDEX_EMBEDDING_DIMENSION="384",
                HF_HUB_OFFLINE="1",
                TRANSFORMERS_OFFLINE="1",
                PYTHONDONTWRITEBYTECODE="1",
                SEARCH_QUERY_EMBEDDING_CACHE_ENABLED="false",
            )

            def audit(event, args):
                if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
                    path = Path(os.fsdecode(args[0])).resolve()
                    if path.name in (".env", ".env.source.local", ".env.local") and path.parent in (
                        root,
                        original,
                    ):
                        raise RuntimeError("Repository dotenv forbidden")
                if event == "socket.connect":
                    address = args[1]
                    local = isinstance(address, tuple) and address[0] in (
                        "127.0.0.1",
                        "::1",
                        "localhost",
                    )
                    selfpipe = any(
                        f.name == "_fallback_socketpair" and f.filename.endswith("socket.py")
                        for f in traceback.extract_stack()
                    )
                    if not (local and (address[1] in (pg_port, mongo_port) or selfpipe)):
                        raise RuntimeError("Connection outside owned test targets forbidden")

            sys.addaudithook(audit)
            print(
                "OWNED_ISOLATION_READY: PG16/Mongo8 random loopback ports, tmpfs, no Dev volumes",
                flush=True,
            )
            import pytest

            result = pytest.main(
                [
                    "-p",
                    "no:cacheprovider",
                    "-q",
                    "--tb=short",
                    "--basetemp",
                    str(Path(temp) / "pytest"),
                    "-s",
                    "tests/integration/test_search_read_path.py",
                ]
            )
            print("PHYSICAL_TEST_EXIT=" + str(int(result)), flush=True)
        finally:
            for identifier in reversed(containers):
                label = docker(
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "etl.readpath.owner" }}',
                    identifier,
                )
                if label != owner:
                    raise RuntimeError("Refusing cleanup of unowned container")
                docker("rm", "-f", "-v", identifier)
            print("OWNED_TEST_CONTAINERS_REMOVED", flush=True)
    raise SystemExit(int(result))


if __name__ == "__main__":
    main()
