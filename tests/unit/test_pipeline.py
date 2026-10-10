from datetime import timedelta

import pytest
from conftest import completed, ingest, make_pdf

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.ports.processing import OCRResult


def test_version_chain_and_immutable_snapshot(container):
    version = ingest(container)
    assert version["version_number"] == 1 and version["parent_version_id"] is None
    assert container.ingestion.run("resource-1", "input", "sample.pdf")["action"] == "NO_CHANGE"
    old_path = container.storage.get_local_path(
        version["storage_bucket"], version["storage_object_key"]
    )
    old_bytes = old_path.read_bytes()
    make_pdf(container, ["Changed"])
    second = container.ingestion.run("resource-1", "input", "sample.pdf")["version"]
    assert second["version_number"] == 2 and second["parent_version_id"] == version["id"]
    make_pdf(container, ["Changed again"])
    third = container.ingestion.run("resource-1", "input", "sample.pdf")["version"]
    assert third["version_number"] == 3 and third["parent_version_id"] == second["id"]
    assert old_path.read_bytes() == old_bytes


@pytest.mark.parametrize(
    "resource,bucket,key", [("missing", "input", "file.pdf"), ("resource-1", "missing", "file.pdf")]
)
def test_ingestion_missing(container, resource, bucket, key):
    container.sync.run(full=True)
    with pytest.raises(DomainError):
        container.ingestion.run(resource, bucket, key)


def test_inactive_ingestion(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update("master_learning_resources", "resource-1", {"is_active": False})
    with pytest.raises(DomainError, match="inactive"):
        container.ingestion.run("resource-1", "input", "sample.pdf")


def test_job_idempotency_and_three_pages(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    assert container.ocr.create_job(version["id"])["id"] == job["id"]
    with container.uow() as repo:
        assert repo.count("ocr_page_tasks", {"job_id": job["id"]}) == 3
    assert job["total_pages"] == 3


@pytest.mark.parametrize("value", [b"", b"not a pdf"])
def test_invalid_pdf(container, value):
    container.sync.run(full=True)
    path = container.storage.get_local_path("input", "bad.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    version = container.ingestion.run("resource-1", "input", "bad.pdf")["version"]
    with pytest.raises(DomainError):
        container.ocr.create_job(version["id"])
    with container.uow() as repo:
        assert repo.count("ocr_jobs", {}) == 0


def test_native_success_and_idempotent_ack(container):
    version = ingest(container, ["Native text"])
    job = container.ocr.create_job(version["id"])
    task = container.ocr.claim("worker")
    result, path = container.ocr.recognize(task)
    assert result.engine_name == "native_pdf_text"
    assert container.ocr.succeed(task, result, path)
    assert not container.ocr.succeed(task, result, path)
    assert not container.ocr.fail(task)
    with container.uow() as repo:
        assert repo.count("ocr_page_results", {}) == 1
        row = repo.get("ocr_jobs", job["id"])
        assert row["completed_pages"] == 1 and row["status"] == "COMPLETED"
        assert row["postprocess_enqueued_at"] is None


def test_retry_and_terminal_count_exactly_once(container):
    version = ingest(container, ["One"])
    job = container.ocr.create_job(version["id"])
    for attempt in range(3):
        task = container.ocr.claim("worker")
        assert task["retry_count"] == attempt
        assert container.ocr.fail(task)
        assert not container.ocr.fail(task)
    assert container.ocr.claim("worker") is None
    with container.uow() as repo:
        row = repo.get("ocr_jobs", job["id"])
        assert row["failed_pages"] == 1 and row["status"] == "COMPLETED_WITH_ERRORS"


def test_stale_recovery_fences_old_worker(container):
    version = ingest(container, ["One"])
    container.ocr.create_job(version["id"])
    old = container.ocr.claim("same-worker-id")
    with container.uow() as repo:
        repo.update("ocr_page_tasks", old["id"], {"heartbeat_at": now() - timedelta(hours=1)})
    assert container.ocr.recover_stale()["recovered"] == 1
    assert container.ocr.recover_stale()["recovered"] == 0
    current = container.ocr.claim("same-worker-id")
    assert current["claim_token"] != old["claim_token"]
    assert not container.ocr.succeed(old, OCRResult("old"))
    assert not container.ocr.heartbeat(old)
    assert container.ocr.heartbeat(current)
    assert container.ocr.succeed(current, OCRResult("current"))


def test_blank_page_fake_path(container):
    version, job = completed(container, [""])
    with container.uow() as repo:
        result = repo.find("ocr_page_results", {})[0]
        assert result["engine_name"] == "fake"
        assert result["result_metadata"]["demo"] is True
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED"


def test_postprocess_order_rerun(container):
    version, job = completed(container)
    first = container.postprocess.run(version["id"])
    with container.uow() as repo:
        before = repo.find("content_units", {}, order=("sequence_no",))
        assert [row["page_from"] for row in before] == [1, 2, 3]
        assert all(row["retrieval_eligible"] is False for row in before)
        assert all(row["review_status"] == "UNREVIEWED" for row in before)
    assert first == container.postprocess.run(version["id"])
    with container.uow() as repo:
        assert before == repo.find("content_units", {}, order=("sequence_no",))
        assert repo.count("content_unit_texts", {}) == 3


def test_postprocess_partial_and_empty_page(container):
    version = ingest(container)
    container.ocr.create_job(version["id"])
    first = container.ocr.claim("w")
    container.ocr.succeed(first, OCRResult("Text"))
    second = container.ocr.claim("w")
    container.ocr.succeed(second, OCRResult("   "))
    for _ in range(3):
        task = container.ocr.claim("w")
        container.ocr.fail(task)
    with pytest.raises(DomainError):
        container.postprocess.run(version["id"])
    result = container.postprocess.run(version["id"], allow_partial=True)
    assert result["content_unit_count"] == 1
    assert result["coverage"] == {"successful_pages": 2, "total_pages": 3, "failed_pages": 1}


def test_postprocess_rollback_after_partial_work(container):
    version, _ = completed(container)
    container.postprocess.run(version["id"])
    with container.uow() as repo:
        before = repo.find("content_unit_texts", {})
    calls = 0

    def broken(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Secret source text must not appear in audit")
        return "changed"

    container.postprocess.normalizer = broken
    with pytest.raises(DomainError):
        container.postprocess.run(version["id"])
    with container.uow() as repo:
        assert before == repo.find("content_unit_texts", {})
        stage = repo.find("etl_stage_runs", {}, order=("-created_at",), limit=1)[0]
        assert stage["status"] == "FAILED"
        assert "Secret source" not in stage["error_message"]


def test_postprocess_requires_terminal_job(container):
    version = ingest(container)
    container.ocr.create_job(version["id"])
    with pytest.raises(DomainError):
        container.postprocess.run(version["id"])


def test_end_to_end_changed_file_and_resume(container):
    version, _ = completed(container)
    container.orchestrator.resume(version["id"])
    assert (
        container.orchestrator.start("resource-1", "input", "sample.pdf")["action"] == "NO_CHANGE"
    )
    make_pdf(container, ["Version two"])
    second = container.orchestrator.start("resource-1", "input", "sample.pdf")["version"]
    assert container.orchestrator.resume(second["id"])["action"] == "WAITING_FOR_WORKER"
    while container.worker.once("w"):
        pass
    container.orchestrator.resume(second["id"])
    assert container.orchestrator.status(second["id"])["content_unit_count"] == 1
    old = container.orchestrator.status(version["id"])
    assert old["content_unit_count"] == 3
    assert old["latest_version"]["id"] == second["id"]


def test_middle_page_gap_preserves_provenance(container):
    version = ingest(container)
    container.ocr.create_job(version["id"])
    first = container.ocr.claim("w")
    container.ocr.succeed(first, OCRResult("page one"))
    container.ocr.max_retries = 1
    second = container.ocr.claim("w")
    container.ocr.fail(second)
    third = container.ocr.claim("w")
    container.ocr.succeed(third, OCRResult("page three"))
    with pytest.raises(DomainError):
        container.postprocess.run(version["id"])
    container.postprocess.run(version["id"], allow_partial=True)
    with container.uow() as repo:
        units = repo.find("content_units", {}, order=("sequence_no",))
        assert [u["sequence_no"] for u in units] == [1, 2]
        assert [u["page_from"] for u in units] == [1, 3]


def test_zero_page_pdf_rejected(container, monkeypatch):
    version = ingest(container)
    monkeypatch.setattr(container.ocr.pdf, "page_count", lambda _: 0)
    with pytest.raises(DomainError, match="Empty PDF"):
        container.ocr.create_job(version["id"])
