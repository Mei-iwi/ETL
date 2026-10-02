"""Exercise the installed CLI boundary across separate processes on an isolated schema."""

import json
import os
import subprocess
import sys

import pytest
from conftest import ROOT, make_pdf
from sqlalchemy import text

pytestmark = pytest.mark.postgres


def test_cli_manual_stages_and_demo_rerun(pg_container):
    container = pg_container
    with container.engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    assert schema.startswith("etl_test_")
    url = container.engine.url.update_query_dict({"options": f"-csearch_path={schema}"})
    environment = os.environ.copy()
    environment.update(
        DATABASE_URL=url.render_as_string(hide_password=False),
        STORAGE_ROOT=str(container.settings.storage_root),
        SOURCE_FIXTURE_ROOT=str(container.settings.source_fixture_root),
        SOURCE_ADAPTER="json",
        OCR_ENGINE="native",
        ADMIN_ENABLED="false",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONUTF8="1",
    )

    def cli(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "data_sync_etl.cli", *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # Never echo raw subprocess output/configuration on failure.
        assert result.returncode == 0, "CLI stage failed; inspect isolated test state"
        return json.loads(result.stdout)

    assert cli("sync-master", "--full")["resources"]["inserted"] == 3
    assert cli("sync-master", "--incremental")["resources"]["records_read"] == 0
    make_pdf(container, ["CLI stage smoke"])
    args = (
        "ingest-resource",
        "--resource-id",
        "resource-2",
        "--bucket",
        "input",
        "--object-key",
        "sample.pdf",
    )
    version = cli(*args)["version"]
    assert cli(*args)["action"] == "NO_CHANGE"
    key = ("--resource-version-id", version["id"])
    job = cli("create-ocr-job", *key)
    assert cli("create-ocr-job", *key)["id"] == job["id"]
    assert cli("run-ocr-worker", "--worker-id", "cli-smoke", "--once") is True
    assert cli("postprocess", *key)["content_unit_count"] == 1
    assert cli("etl-status", *key)["ocr_job"]["status"] == "COMPLETED"
    assert cli("recover-stale-ocr-tasks")["recovered"] == 0
    assert cli("resume-etl", *key)["content_unit_count"] == 1
    assert cli("demo-run", "--fixture-resource-id", "resource-1")["content_unit_count"] == 3
    assert cli("demo-run", "--fixture-resource-id", "resource-1")["action"] == "NO_CHANGE"
    with container.uow() as repo:
        assert repo.count("sync_runs", {}) == 8  # two explicit syncs; no implicit demo full sync
