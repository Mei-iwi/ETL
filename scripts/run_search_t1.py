"""Run conditional T1 gates only on owned temporary PG/Mongo; no dotenv or Dev URLs."""


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
    parser.add_argument("--phase", choices=("preflight", "acceptance", "recovery", "extra", "invariants", "diagnose", "verify", "benchmark", "t2-targeted", "t2-benchmark", "t2-concurrency"), required=True)
    parser.add_argument("--run-tag", default="")
    args = parser.parse_args()
    if args.run_tag and not re.fullmatch(r'[a-z0-9-]{1,30}', args.run_tag):
        parser.error('Invalid run tag')
    model_path = args.model_path.resolve(strict=True)
    if not model_path.is_dir():
        parser.error("Existing local model directory required")
    root = Path(__file__).resolve().parents[1]
    if Path.cwd().resolve() != root:
        parser.error("Run from repository root")
    names = {"preflight": "T5_PREFLIGHT", "acceptance": "T1_ACCEPTANCE", "recovery": "T1_RECOVERY_ACCEPTANCE", "extra": "T1_RECOVERY_EXTRA", "invariants": "T1_RECOVERY_INVARIANTS", "benchmark": "T1_BENCHMARK_RECOVERY"}
    names.update(diagnose='T1_DIAGNOSE', verify='T1_HTTP_VERIFICATION')
    names.update({'t2-targeted': 'T2_TARGETED', 't2-benchmark': 'T2_BENCHMARK'})
    names['t2-concurrency'] = 'T2_CONCURRENCY'
    if args.phase == 'benchmark':
        import hashlib
        import json

        folder = root / 'docs/audits/search-pg-text-batch'
        try:
            race = json.loads((folder / 'T1_RECOVERY_ACCEPTANCE.json').read_text())
            extra = json.loads((folder / 'T1_RECOVERY_EXTRA.json').read_text())
            invariants = json.loads((folder / 'T1_RECOVERY_INVARIANTS.json').read_text())
            assert race['status'] == 'RACES_PASS_REMAINING_GATES_PENDING'
            assert race['stable_pairs'] == 54 and len(race['races']) == 42
            assert extra['status'] == 'EXTRA_GATES_PASS'
            assert invariants['status'] == 'INVARIANTS_PASS'
            assert all(hashlib.sha256((root / path).read_bytes()).hexdigest() == checksum
                       for path, checksum in race['source_hashes'].items())
        except (OSError, ValueError, KeyError, AssertionError):
            parser.error('Recovery safety gates missing, failed or source changed; benchmark forbidden')
    folder = 'search-mongo-batch' if args.phase.startswith('t2-') else 'search-pg-text-batch'
    if args.phase == 't2-benchmark':
        import hashlib
        import json

        proofs = []
        for path in (root / 'docs/audits' / folder).glob('T2_TARGETED*.json'):
            value = json.loads(path.read_text())
            if (value.get('status') == 'TARGETED_PASS' and value.get('static_pairs') == 54
                    and len(value.get('races', [])) == 84 and value.get('source_hashes')
                    and all(hashlib.sha256((root / key).read_bytes()).hexdigest() == checksum
                            for key, checksum in value['source_hashes'].items())):
                proofs.append(value)
        if not proofs:
            parser.error('T2 targeted parity/race evidence missing or code changed; benchmark forbidden')
    target = root / "docs/audits" / folder / (
        names[args.phase] + ("_" + args.run_tag if args.run_tag else "") + ".json"
    )
    if target.exists():
        parser.error("Refusing to overwrite previous recovery metrics")
    target.parent.mkdir(parents=True, exist_ok=True)
    original = Path(subprocess.check_output(["git", "rev-parse", "--git-common-dir"], text=True).strip()).resolve().parent
    sys.path.insert(0, str(root / "src"))
    sys.path.insert(0, str(root))
    from data_sync_etl.config import Settings
    from tests.search_t1_support import awake_test_host

    Settings.model_config["env_file"] = None
    owner = uuid.uuid4().hex
    containers = []

    def docker(*args):
        result = subprocess.run(["docker", *args], capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError("Owned Docker operation failed; exit=" + str(result.returncode))
        return result.stdout.strip()

    with awake_test_host(), tempfile.TemporaryDirectory(prefix="etl-t1-isolation-") as temp:
        os.environ["DOCKER_CONFIG"] = temp
        try:
            password = uuid.uuid4().hex
            pg = docker(
                "run",
                "--pull",
                "never",
                "-d",
                "--label",
                "etl.t1.owner=" + owner,
                "--name",
                "etl-t1-pg-" + owner[:10],
                "--tmpfs",
                "/var/lib/postgresql/data",
                "-e",
                "POSTGRES_USER=etl_test_reader",
                "-e",
                "POSTGRES_DB=etl_test_t1",
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
                "etl.t1.owner=" + owner,
                "--name",
                "etl-t1-mongo-" + owner[:10],
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
                        dbname="etl_test_t1",
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
                + "/etl_test_t1",
                TEST_MONGO_URI="mongodb://127.0.0.1:" + str(mongo_port),
                ETL_REQUIRE_POSTGRES="1",
                ETL_T1_PHYSICAL="1",
                ETL_T1_PHASE=args.phase,
                ETL_READPATH_HEAD=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                ETL_QUERY_CACHE_PHYSICAL="1",
                ETL_SEARCH_REAL_MODEL="1",
                ETL_QUERY_CACHE_MODEL_PATH=str(model_path),
                ETL_T1_OUTPUT=str(target),
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
                    "tests/integration/test_search_t2_physical.py::test_t2_concurrency" if args.phase == "t2-concurrency" else
                    "tests/integration/test_search_t2_physical.py::test_t2_targeted" if args.phase == "t2-targeted" else
                    "tests/integration/test_search_t2_physical.py::test_t2_benchmark" if args.phase == "t2-benchmark" else
                    "tests/integration/test_search_t1_recovery.py::test_race_semantics_recovery" if args.phase == "recovery" else
                    "tests/integration/test_search_t1_recovery.py::test_concurrency_and_access_recovery" if args.phase == "extra" else
                    "tests/integration/test_search_t1_recovery.py::test_postgres_invariants_recovery" if args.phase == "invariants" else
                    "tests/integration/test_search_t1_recovery.py::test_timeout_diagnosis" if args.phase == "diagnose" else
                    "tests/integration/test_search_t1_recovery.py::test_http_activation_verification" if args.phase == "verify" else
                    "tests/integration/test_search_t1_recovery.py::test_benchmark_recovery" if args.phase == "benchmark" else
                    "tests/integration/test_search_t1_physical.py::" + (
                        "test_t5_baseline_preflight" if args.phase == "preflight" else
                        "test_t1_acceptance" if args.phase == "acceptance" else "test_t1_benchmark"),
                ]
            )
            print("PHYSICAL_TEST_EXIT=" + str(int(result)), flush=True)
        finally:
            for identifier in reversed(containers):
                label = docker(
                    "inspect",
                    "--format",
                    '{{ index .Config.Labels "etl.t1.owner" }}',
                    identifier,
                )
                if label != owner:
                    raise RuntimeError("Refusing cleanup of unowned container")
                docker("rm", "-f", "-v", identifier)
            print("OWNED_TEST_CONTAINERS_REMOVED", flush=True)
    raise SystemExit(int(result))


if __name__ == "__main__":
    main()
