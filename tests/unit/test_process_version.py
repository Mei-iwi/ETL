import pytest
from conftest import ingest
from fastapi.testclient import TestClient

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.main import create_app
from data_sync_etl.ports.processing import OCRResult


def test_process_version_creates_job(
    container,
    pdf_file,
):
    container.settings.admin_enabled = True

    container.sync.run(full=True)

    version = container.ingestion.run(
        "resource-1",
        "input",
        "sample.pdf",
    )["version"]

    url = f"/api/v1/resource-versions/{version['id']}/process"

    with TestClient(create_app(container)) as client:
        response = client.post(url)

        assert response.status_code == 202
        data = response.json()

        assert data["resource_version_id"] == version["id"]

        assert data["job_status"] == "PENDING"
        assert data["action"] == "QUEUED"

        repeated = client.post(url)

        assert repeated.status_code == 202
        assert repeated.json()["job_id"] == data["job_id"]

    with container.uow() as repo:
        jobs = repo.find(
            "ocr_jobs",
            {"resource_version_id": version["id"]},
        )

        tasks = repo.find(
            "ocr_page_tasks",
            {"job_id": data["job_id"]},
        )

    assert len(jobs) == 1
    assert len(tasks) == 3


class FakeDocumentStore:
    def __init__(self):
        self.pages = {}
        self.units = {}

    def upsert_ocr_page(self, document):
        self.pages[document["_id"]] = document

    def upsert_content_unit(self, document):
        self.units[document["_id"]] = document

    def reconcile_content_units(self, version_id, unit_ids, generation=None):
        for key, document in self.units.items():
            if (
                document["resource_version_id"] == version_id
                and key not in unit_ids
                and (generation is None or document.get("projection_generation", 0) <= generation)
            ):
                document["is_current"] = False


def test_process_version_end_to_end(
    container,
    pdf_file,
):
    container.settings.admin_enabled = True
    container.sync.run(full=True)

    version = container.ingestion.run(
        "resource-1",
        "input",
        "sample.pdf",
    )["version"]

    fake = FakeDocumentStore()

    container.mongo_projector.documents = fake

    with TestClient(create_app(container)) as client:
        response = client.post(f"/api/v1/resource-versions/{version['id']}/process")
    assert response.status_code == 202

    while container.worker.once("test-worker"):
        pass

    assert len(fake.pages) == 3
    assert len(fake.units) == 3

    with container.uow() as repo:
        stages = repo.find(
            "etl_stage_runs",
            {
                "resource_version_id": version["id"],
                "stage": "MONGO_PROJECTION",
            },
        )

    assert any(stage["status"] == "COMPLETED" for stage in stages)


def finish_ocr(container, texts=None):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    for text in texts or ["Tiếng Việt\nDòng thứ hai", "Trang 2", "Trang 3"]:
        task = container.ocr.claim("test")
        container.ocr.succeed(task, OCRResult(text, 0.9, "test-engine", "1"))
    return version, job


def test_request_does_not_finalize_terminal_job(container, monkeypatch):
    version, job = finish_ocr(container)
    container.settings.admin_enabled = True

    def forbidden(*args, **kwargs):
        pytest.fail("HTTP must not perform background work")

    monkeypatch.setattr(container.ocr, "recognize", forbidden)
    monkeypatch.setattr(container.postprocess, "run", forbidden)
    monkeypatch.setattr(container.mongo_projector, "project", forbidden)
    with TestClient(create_app(container)) as client:
        result = client.post(f"/api/v1/resource-versions/{version['id']}/process")
        assert result.status_code == 202
        assert result.json()["job_id"] == job["id"]
        assert result.json()["processing_status"] == "FINALIZATION_PENDING"
        assert client.get(result.json()["status_url"]).status_code == 200
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {}) == 0


@pytest.mark.parametrize("change", [{"is_active": False}, {"deleted_at": now()}])
def test_process_unavailable_resource(container, change):
    version = ingest(container)
    container.settings.admin_enabled = True
    with container.uow() as repo:
        repo.update("master_learning_resources", "resource-1", change)
    with TestClient(create_app(container)) as client:
        assert client.post(f"/api/v1/resource-versions/{version['id']}/process").status_code == 400
    with container.uow() as repo:
        assert repo.count("ocr_jobs", {}) == 0


def test_process_missing_and_admin_guard(container):
    with TestClient(create_app(container)) as client:
        assert client.post("/api/v1/resource-versions/missing/process").status_code == 403
        container.settings.admin_enabled = True
        assert client.post("/api/v1/resource-versions/missing/process").status_code == 404
        assert client.post("/api/v1/resource-versions/" + "x" * 31 + "/process").status_code == 422


def test_invalid_pdf_is_not_accepted(container):
    container.sync.run(full=True)
    path = container.storage.get_local_path("input", "bad.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not a PDF")
    version = container.ingestion.run("resource-1", "input", "bad.pdf")["version"]
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        assert client.post(f"/api/v1/resource-versions/{version['id']}/process").status_code == 400
    with container.uow() as repo:
        assert repo.count("ocr_jobs", {}) == 0


def test_raw_text_provenance_and_idempotency(container):
    version, job = finish_ocr(container)
    assert container.worker.once("reconciler")
    assert not container.worker.once("reconciler")
    with container.uow() as repo:
        for task in repo.find("ocr_page_tasks", {"job_id": job["id"]}):
            source = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
            doc = container.documents.pages[f"{job['id']}:{task['page_num']}"]
            assert doc["raw_text"] == source["raw_text"]
            assert doc["engine_name"] == "test-engine"
            assert doc["confidence"] == pytest.approx(0.9)
            assert doc["resource_version_id"] == version["id"]
        for unit in repo.find("content_units", {"resource_version_id": version["id"]}):
            body = repo.get("content_unit_texts", unit["id"])
            assert container.documents.units[unit["id"]]["page_result_id"] == body["page_result_id"]
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 1
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"


def test_partial_job_never_ready(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    assert container.processing_finalizer.finalize(job["id"]) is None
    container.ocr.max_retries = 1
    container.ocr.succeed(container.ocr.claim("w"), OCRResult("good"))
    container.ocr.fail(container.ocr.claim("w"))
    container.ocr.succeed(container.ocr.claim("w"), OCRResult("good"))
    assert container.processing_finalizer.finalize(job["id"]) is None
    assert not container.worker.once("recovery")
    with pytest.raises(DomainError):
        container.postprocess.run(version["id"])
    with pytest.raises(DomainError):
        container.mongo_projector.project(version["id"], job["id"])
    status = container.orchestrator.status(version["id"])
    assert status["processing_status"] == "OCR_FAILED"
    assert status["ocr_job"]["completed_pages"] == 2
    assert status["ocr_job"]["failed_pages"] == 1
    assert not container.documents.pages


@pytest.mark.parametrize("fail_after", [0, 1])
def test_projection_retry_after_partial_write(container, monkeypatch, fail_after):
    from datetime import timedelta

    version, job = finish_ocr(container)
    original = container.documents.upsert_ocr_page
    calls = 0

    def unavailable(document):
        nonlocal calls
        if calls == fail_after:
            raise RuntimeError("secret-connection-string")
        calls += 1
        original(document)

    monkeypatch.setattr(container.documents, "upsert_ocr_page", unavailable)
    assert container.worker.once("recovery")
    assert len(container.documents.pages) == fail_after
    assert not container.worker.once("backoff")
    with container.uow() as repo:
        stage = repo.find("etl_stage_runs", {"stage": "MONGO_PROJECTION"})[0]
        assert stage["status"] == "FAILED"
        assert "secret" not in stage["error_message"]
        assert repo.count("ocr_page_results", {}) == 3
        assert repo.count("content_unit_texts", {}) == 3
        repo.update("etl_stage_runs", stage["id"], {"finished_at": now() - timedelta(minutes=5)})
    monkeypatch.setattr(container.documents, "upsert_ocr_page", original)
    assert container.worker.once("recovery")
    assert len(container.documents.pages) == 3
    assert len(container.documents.units) == 3
    assert container.process_version.run(version["id"])["job_id"] == job["id"]
    assert container.process_version.run(version["id"])["processing_status"] == "CONTENT_READY"


def test_abandoned_running_stage_recovered_without_pages(container):
    from datetime import timedelta

    version, job = finish_ocr(container)
    with container.uow() as repo:
        old = repo.insert(
            "etl_stage_runs",
            {
                "resource_version_id": version["id"],
                "stage": "MONGO_PROJECTION",
                "status": "RUNNING",
                "started_at": now() - timedelta(hours=1),
                "created_at": now(),
            },
        )
    assert container.worker.once("replacement")
    with container.uow() as repo:
        assert repo.get("etl_stage_runs", old["id"])["status"] == "FAILED"
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION", "status": "RUNNING"}) == 0
        assert repo.get("ocr_jobs", job["id"])["completed_pages"] == 3


def test_two_finalizers_do_not_overlap(container, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    version, job = finish_ocr(container)
    entered, release = Event(), Event()
    original = container.mongo_projector.project

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(container.mongo_projector, "project", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(container.processing_finalizer.finalize, job["id"])
        try:
            assert entered.wait(10)
            second = pool.submit(container.processing_finalizer.finalize, job["id"])
            assert second.result(timeout=5)["status"] == "IN_PROGRESS"
        finally:
            release.set()
        assert first.result(timeout=10)["status"] == "CONTENT_READY"
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 1


def test_manual_postprocess_cannot_overlap_projection(container, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    version, job = finish_ocr(container)
    entered, release = Event(), Event()
    original = container.mongo_projector.project

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(container.mongo_projector, "project", pause)
    container.settings.admin_enabled = True
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(container.processing_finalizer.finalize, job["id"])
        try:
            assert entered.wait(10)
            with pytest.raises(DomainError, match="already running"):
                container.postprocess.run(version["id"])
            with TestClient(create_app(container)) as api:
                response = api.post(f"/admin/resource-versions/{version['id']}/postprocess")
                assert response.status_code == 400
        finally:
            release.set()
        assert running.result(timeout=10)["status"] == "CONTENT_READY"
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "POSTPROCESS"}) == 1


def test_worker_auto_recovers_stale_task_without_pending_tasks(container):
    from datetime import timedelta

    version = ingest(container, ["one"])
    job = container.ocr.create_job(version["id"])
    stale = container.ocr.claim("crashed")
    with container.uow() as repo:
        repo.update(
            "ocr_page_tasks",
            stale["id"],
            {
                "heartbeat_at": now() - timedelta(hours=1),
            },
        )
    assert container.worker.once("replacement")
    with container.uow() as repo:
        task = repo.get("ocr_page_tasks", stale["id"])
        assert task["status"] == "SUCCEEDED"
        assert task["retry_count"] == 1
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED"


def test_worker_does_not_recover_live_task(container):
    version = ingest(container, ["one"])
    container.ocr.create_job(version["id"])
    active = container.ocr.claim("live")
    assert not container.worker.once("other")
    with container.uow() as repo:
        task = repo.get("ocr_page_tasks", active["id"])
        assert task["status"] == "RUNNING"
        assert task["retry_count"] == 0


def test_old_projection_stops_writing_after_takeover(container, monkeypatch):
    version, job = finish_ocr(container)
    with container.uow() as repo:
        old = repo.insert(
            "etl_stage_runs",
            {
                "resource_version_id": version["id"],
                "stage": "MONGO_PROJECTION",
                "status": "RUNNING",
                "started_at": now(),
                "created_at": now(),
            },
        )
    original = container.documents.upsert_ocr_page
    written = 0

    def takeover(document):
        nonlocal written
        original(document)
        written += 1
        if written == 1:
            with container.uow() as repo:
                repo.update("etl_stage_runs", old["id"], {"status": "FAILED"})
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

    monkeypatch.setattr(container.documents, "upsert_ocr_page", takeover)
    with pytest.raises(DomainError, match="ownership lost"):
        container.mongo_projector.project(version["id"], job["id"], old["id"], 1)
    assert written == 1
    assert len(container.documents.pages) == 1
    assert not container.documents.units


def test_retry_limit_and_callback_preserve_ocr(container, monkeypatch, caplog):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    container.processing_finalizer.max_attempts = 1

    def broken(*args):
        raise RuntimeError("private-password-and-document-body")

    monkeypatch.setattr(container.mongo_projector, "project", broken)
    for _ in range(3):
        assert container.worker.once("worker")
    assert not container.worker.once("idle")
    assert "private-password" not in caplog.text
    with container.uow() as repo:
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED"
        assert repo.count("ocr_page_tasks", {"status": "SUCCEEDED"}) == 3
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 1


def test_missing_successful_result_is_controlled(container):
    version, job = finish_ocr(container)
    with container.uow() as repo:
        row = repo.find("ocr_page_results", {})[0]
        repo.delete("ocr_page_results", {"id": row["id"]})
    with pytest.raises(DomainError):
        container.mongo_projector.project(version["id"], job["id"])


def test_worker_crash_mid_projection_is_recovered(container, monkeypatch):
    version, job = finish_ocr(container)
    original = container.documents.upsert_ocr_page
    calls = 0

    class SimulatedCrash(BaseException):
        pass

    def crash(document):
        nonlocal calls
        original(document)
        calls += 1
        if calls == 1:
            raise SimulatedCrash()

    monkeypatch.setattr(container.documents, "upsert_ocr_page", crash)
    with pytest.raises(SimulatedCrash):
        container.processing_finalizer.finalize(job["id"])
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION", "status": "RUNNING"}) == 1
    monkeypatch.setattr(container.documents, "upsert_ocr_page", original)
    assert container.worker.once("replacement")
    assert len(container.documents.pages) == 3
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"


def test_changed_units_reconcile_without_deleting_history(container):
    version, job = finish_ocr(container)
    assert container.worker.once("first")
    before = set(container.documents.units)
    with container.uow() as repo:
        task = repo.find("ocr_page_tasks", {"job_id": job["id"], "page_num": 3})[0]
        result = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
        repo.update("ocr_page_results", result["id"], {"raw_text": "  "})
    container.postprocess.run(version["id"])
    assert container.orchestrator.status(version["id"])["processing_status"] != "CONTENT_READY"
    assert container.worker.once("reconcile")
    assert set(container.documents.units) == before
    current = [d for d in container.documents.units.values() if d["is_current"]]
    assert len(current) == 2


def test_process_internal_error_redaction(container, monkeypatch, caplog):
    container.settings.admin_enabled = True

    def broken(*args):
        raise RuntimeError("private-connection-string")

    monkeypatch.setattr(container.process_version, "run", broken)
    with TestClient(create_app(container)) as client:
        response = client.post("/api/v1/resource-versions/example/process")
    assert response.status_code == 500
    assert response.headers["X-Correlation-ID"]
    assert "private-connection-string" not in response.text + caplog.text
