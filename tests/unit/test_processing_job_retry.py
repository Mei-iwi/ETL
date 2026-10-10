from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event

import pytest
from conftest import ingest
from fastapi.testclient import TestClient

from data_sync_etl.domain.core import ProcessingRetryConflict, now
from data_sync_etl.main import create_app
from data_sync_etl.ports.processing import OCRResult


def failed_ocr(container, failed_pages=(2,)):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    container.ocr.max_retries = 1
    for page in range(1, 4):
        task = container.ocr.claim("original")
        assert task["page_num"] == page
        if page in failed_pages:
            assert container.ocr.fail(task)
        else:
            assert container.ocr.succeed(task, OCRResult(f"Page {page}"))
    return version, job


def completed_ocr(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    for page in range(1, 4):
        assert container.ocr.succeed(container.ocr.claim("original"), OCRResult(f"Page {page}"))
    return version, job


def retry_api(container, job_id):
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        return client.post(f"/api/v1/processing-jobs/{job_id}/retry")


@pytest.mark.parametrize("failed_pages", [(2,), (2, 3)])
def test_retry_only_failed_ocr_pages_and_worker_finishes(container, failed_pages):
    version, job = failed_ocr(container, failed_pages)
    with container.uow() as repo:
        original = {
            task["page_num"]: repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]["id"]
            for task in repo.find("ocr_page_tasks", {"job_id": job["id"]})
            if task["status"] == "SUCCEEDED"
        }
    response = retry_api(container, job["id"])
    assert response.status_code == 202
    body = response.json()
    assert body == {
        "job_id": job["id"],
        "resource_version_id": version["id"],
        "job_status": "PENDING",
        "processing_status": "OCR_PENDING",
        "action": "OCR_REQUEUED",
        "status_url": f"/admin/resource-versions/{version['id']}/etl-status",
    }
    with container.uow() as repo:
        tasks = repo.find("ocr_page_tasks", {"job_id": job["id"]}, order=("page_num",))
        assert repo.count("ocr_jobs", {}) == 1
        assert len(tasks) == 3
        assert [task["page_num"] for task in tasks if task["status"] == "PENDING"] == list(
            failed_pages
        )
        assert all(task["retry_count"] == 0 for task in tasks if task["status"] == "PENDING")
        current = repo.get("ocr_jobs", job["id"])
        assert current["completed_pages"] == len(original)
        assert current["failed_pages"] == 0
        assert repo.count("etl_stage_runs", {"stage": "OCR_RETRY"}) == 1
    for _ in failed_pages:
        assert container.worker.once("replacement")
    with container.uow() as repo:
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED"
        for task in repo.find("ocr_page_tasks", {"job_id": job["id"]}):
            result = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
            if task["page_num"] in original:
                assert result["id"] == original[task["page_num"]]
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"


def test_retry_api_errors_and_no_inline_work(container, monkeypatch):
    version, job = failed_ocr(container)
    with TestClient(create_app(container)) as client:
        assert client.post(f"/api/v1/processing-jobs/{job['id']}/retry").status_code == 403
        container.settings.admin_enabled = True
        assert client.post("/api/v1/processing-jobs/missing/retry").status_code == 404
        assert client.post("/api/v1/processing-jobs/" + "x" * 31 + "/retry").status_code == 422

        def forbidden(*args, **kwargs):
            pytest.fail("HTTP must not do background work")

        monkeypatch.setattr(container.ocr, "recognize", forbidden)
        monkeypatch.setattr(container.postprocess, "run", forbidden)
        monkeypatch.setattr(container.mongo_projector, "project", forbidden)
        first = client.post(f"/api/v1/processing-jobs/{job['id']}/retry")
        second = client.post(f"/api/v1/processing-jobs/{job['id']}/retry")
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["action"] == "ALREADY_IN_PROGRESS"
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "OCR_RETRY"}) == 1
        assert repo.get("ocr_jobs", job["id"])["status"] == "PENDING"
        assert repo.get("resource_versions", version["id"])


def test_manual_ocr_retry_budget_is_durable(container):
    _, job = failed_ocr(container)
    container.retry_processing_job.max_manual_retries = 1
    assert retry_api(container, job["id"]).status_code == 202
    task = container.ocr.claim("failed-again")
    assert container.ocr.fail(task)
    assert retry_api(container, job["id"]).status_code == 409
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "OCR_RETRY"}) == 1
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED_WITH_ERRORS"


def test_running_ocr_job_is_not_reset(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    claimed = container.ocr.claim("live")
    response = retry_api(container, job["id"])
    assert response.status_code == 200
    assert response.json()["action"] == "ALREADY_IN_PROGRESS"
    with container.uow() as repo:
        assert repo.get("ocr_page_tasks", claimed["id"])["status"] == "RUNNING"
        assert repo.count("etl_stage_runs", {}) == 0


def test_finalization_retry_is_durable_and_does_not_reocr(container, monkeypatch):
    version, job = completed_ocr(container)
    container.processing_finalizer.max_attempts = 1
    original = container.mongo_projector.project

    def unavailable(*args):
        raise RuntimeError("MongoDB unavailable")

    monkeypatch.setattr(container.mongo_projector, "project", unavailable)
    with pytest.raises(RuntimeError):
        container.processing_finalizer.finalize(job["id"])
    monkeypatch.setattr(container.mongo_projector, "project", original)
    assert (
        container.orchestrator.status(version["id"])["processing_status"] == "FINALIZATION_FAILED"
    )
    response = retry_api(container, job["id"])
    assert response.status_code == 202
    assert response.json()["action"] == "FINALIZATION_REQUEUED"
    assert response.json()["processing_status"] == "FINALIZATION_PENDING"
    assert retry_api(container, job["id"]).status_code == 200
    with container.uow() as repo:
        assert (
            repo.count("etl_stage_runs", {"stage": "FINALIZATION_RETRY", "status": "PENDING"}) == 1
        )
        assert repo.count("ocr_page_results", {}) == 3
    assert container.worker.once("idle-reconciler")
    with container.uow() as repo:
        assert (
            repo.count("etl_stage_runs", {"stage": "FINALIZATION_RETRY", "status": "COMPLETED"})
            == 1
        )
        assert repo.count("ocr_page_results", {}) == 3
        assert repo.count("etl_stage_runs", {"stage": "POSTPROCESS"}) == 1
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 2
    assert len(container.documents.pages) == 3
    assert len(container.documents.units) == 3
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
    assert retry_api(container, job["id"]).json()["action"] == "ALREADY_COMPLETED"


def test_failed_postprocess_retries_without_ocr(container):
    version, job = completed_ocr(container)
    container.processing_finalizer.max_attempts = 1
    original = container.postprocess.normalizer

    def broken(_):
        raise ValueError("normalization error")

    container.postprocess.normalizer = broken
    with pytest.raises(ValueError, match="Postprocess failed; existing content preserved"):
        container.processing_finalizer.finalize(job["id"])
    container.postprocess.normalizer = original
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "POSTPROCESS", "status": "FAILED"}) == 1
    assert retry_api(container, job["id"]).status_code == 202
    assert container.worker.once("postprocess-retry")
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "POSTPROCESS", "status": "COMPLETED"}) == 1
        assert repo.count("ocr_page_results", {}) == 3
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"


def test_finalization_retry_exhaustion_and_second_manual_cycle(container, monkeypatch):
    version, job = completed_ocr(container)
    container.processing_finalizer.max_attempts = 1

    def unavailable(*args):
        raise RuntimeError("MongoDB unavailable")

    original = container.mongo_projector.project
    monkeypatch.setattr(container.mongo_projector, "project", unavailable)
    with pytest.raises(RuntimeError):
        container.processing_finalizer.finalize(job["id"])
    assert retry_api(container, job["id"]).status_code == 202
    assert container.worker.once("retry-fails")
    with container.uow() as repo:
        assert (
            repo.count("etl_stage_runs", {"stage": "FINALIZATION_RETRY", "status": "FAILED"}) == 1
        )
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 2
    assert (
        container.orchestrator.status(version["id"])["processing_status"] == "FINALIZATION_FAILED"
    )
    monkeypatch.setattr(container.mongo_projector, "project", original)
    assert retry_api(container, job["id"]).status_code == 202
    assert container.worker.once("retry-succeeds")
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "FINALIZATION_RETRY"}) == 2
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 3
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"


def test_two_retry_requests_do_not_duplicate_stage(container):
    _, job = failed_ocr(container)

    def request_retry():
        try:
            return container.retry_processing_job.run(job["id"])["action"]
        except ProcessingRetryConflict:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request_retry(), range(2)))
    assert "OCR_REQUEUED" in results
    assert set(results) <= {"OCR_REQUEUED", "ALREADY_IN_PROGRESS", "CONFLICT"}
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "OCR_RETRY"}) == 1


def test_retry_conflicts_with_live_finalizer(container, monkeypatch):
    version, job = completed_ocr(container)
    entered, release = Event(), Event()
    original = container.mongo_projector.project

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(container.mongo_projector, "project", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        running = pool.submit(container.processing_finalizer.finalize, job["id"])
        try:
            assert entered.wait(10)
            assert retry_api(container, job["id"]).status_code == 409
        finally:
            release.set()
        assert running.result(timeout=10)["status"] == "CONTENT_READY"
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"


def test_abandoned_retry_projection_is_recovered(container):
    version, job = completed_ocr(container)
    container.processing_finalizer.max_attempts = 3
    with container.uow() as repo:
        failed = repo.insert(
            "etl_stage_runs",
            {
                "resource_version_id": version["id"],
                "stage": "MONGO_PROJECTION",
                "status": "FAILED",
                "started_at": now() - timedelta(hours=1),
                "finished_at": now() - timedelta(hours=1),
                "created_at": now() - timedelta(hours=1),
            },
        )
    assert retry_api(container, job["id"]).status_code == 202
    with container.uow() as repo:
        repo.insert(
            "etl_stage_runs",
            {
                "resource_version_id": version["id"],
                "stage": "MONGO_PROJECTION",
                "status": "RUNNING",
                "started_at": now(),
                "created_at": now(),
            },
        )
    assert container.worker.once("replacement")
    with container.uow() as repo:
        assert repo.get("etl_stage_runs", failed["id"])["status"] == "FAILED"
        assert (
            repo.count("etl_stage_runs", {"stage": "FINALIZATION_RETRY", "status": "COMPLETED"})
            == 1
        )
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
