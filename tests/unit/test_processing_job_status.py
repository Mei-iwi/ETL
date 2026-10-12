from conftest import completed, ingest
from fastapi.testclient import TestClient

from data_sync_etl.main import create_app


def test_processing_job_status_pending(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])

    with TestClient(create_app(container)) as client:
        response = client.get(f"/api/v1/processing-jobs/{job['id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job["id"]
    assert data["resource_version_id"] == version["id"]
    assert data["status"] == "PENDING"
    assert data["processing_status"] == "OCR_PENDING"
    assert data["total_pages"] == 3
    assert data["completed_pages"] == 0
    assert data["failed_pages"] == 0
    assert isinstance(data["stage_runs"], list)


def test_processing_job_status_running(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    container.ocr.claim("status-test-worker")

    with TestClient(create_app(container)) as client:
        response = client.get(f"/api/v1/processing-jobs/{job['id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "RUNNING"
    assert data["processing_status"] == "OCR_PENDING"


def test_processing_job_status_failed_terminal(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    with container.uow() as repo:
        repo.update("ocr_jobs", job["id"], {"status": "FAILED"})

    with TestClient(create_app(container)) as client:
        response = client.get(f"/api/v1/processing-jobs/{job['id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "FAILED"
    assert data["processing_status"] == "OCR_FAILED"


def test_processing_job_status_content_ready(container):
    version, job = completed(container)

    with TestClient(create_app(container)) as client:
        response = client.get(f"/api/v1/processing-jobs/{job['id']}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job["id"]
    assert data["resource_version_id"] == version["id"]
    assert data["status"] == "COMPLETED"
    assert data["processing_status"] == "CONTENT_READY"
    assert data["completed_pages"] == data["total_pages"] == 3
    assert data["failed_pages"] == 0


def test_processing_job_status_not_found(container):
    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/processing-jobs/not-found")

    assert response.status_code == 404
    assert response.json()["detail"] == "Processing job not found"


def test_processing_job_status_rejects_invalid_id(container):
    with TestClient(create_app(container)) as client:
        response = client.get(f"/api/v1/processing-jobs/{'x' * 31}")

    assert response.status_code == 422


def test_processing_job_status_is_read_only(container):
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    with container.uow() as repo:
        before = repo.get("ocr_jobs", job["id"])

    with TestClient(create_app(container)) as client:
        assert client.get(f"/api/v1/processing-jobs/{job['id']}").status_code == 200

    with container.uow() as repo:
        after = repo.get("ocr_jobs", job["id"])
        assert repo.count("ocr_jobs", {}) == 1
    assert after == before


def test_processing_job_status_has_processing_openapi_tag(container):
    schema = create_app(container).openapi()

    operation = schema["paths"]["/api/v1/processing-jobs/{job_id}"]["get"]
    assert operation["tags"] == ["Multimedia Data Processing and Normalization"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema["$ref"].endswith("/ProcessingJobStatusResponse")
