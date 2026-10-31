from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def test_source_files_require_admin_and_postgresql(container):
    with TestClient(create_app(container), client=("127.0.0.1", 12345)) as client:
        assert client.get("/api/v1/master-data/sync/files").status_code == 403
        container.settings.admin_enabled = True
        response = client.get("/api/v1/master-data/sync/files")
        assert response.status_code == 200
        assert response.json()["file_sync_error"] == "SOURCE_ORIGINAL_IMPORT_DISABLED"
        assert client.get("/api/v1/master-data/sync/files?size=101").status_code == 422


def test_source_files_safe_contract(container, monkeypatch):
    container.settings.admin_enabled = True
    calls = []

    def read(after, size):
        calls.append((after, size))
        return {
            "items": [{
                "source_version_id": "version-1", "resource_id": "resource-1",
                "resource_title": "PDF", "source_version_number": 1,
                "source_lifecycle_status": "UPLOADED", "updated_at": now(),
                "storage_bucket": "private-bucket", "storage_object_key": "private-path",
                "source_hash": "private-hash",
            }],
            "next_cursor": "version-1",
        }

    monkeypatch.setattr(container.source, "list_versions", read, raising=False)
    with TestClient(create_app(container), client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/v1/master-data/sync/files?after=previous&size=1")
        assert response.status_code == 200
        assert calls == [("previous", 1)]
        data = response.json()
        assert data["file_sync_error"] == "SOURCE_ORIGINAL_IMPORT_DISABLED"
        assert data["items"][0]["import_status"] == "UNTRACKED"
        assert data["items"][0]["retry_eligible"] is False
        assert "private-" not in response.text
        operation = client.get("/openapi.json").json()["paths"]["/api/v1/master-data/sync/files"]["get"]
        assert operation["tags"] == ["Learning Resource Ingestion and Management"]
        status = client.get("/api/v1/master-data/sync/status").json()
        assert status["file_sync_supported"] is False
        assert status["file_sync_error"] == "SOURCE_ORIGINAL_IMPORT_DISABLED"


def test_source_files_error_hides_internal_details(container, monkeypatch):
    container.settings.admin_enabled = True

    def fail(**kwargs):
        raise DomainError("private-connection-and-path")

    monkeypatch.setattr(container.source, "list_versions", fail, raising=False)
    with TestClient(create_app(container), client=("127.0.0.1", 12345)) as client:
        response = client.get("/api/v1/master-data/sync/files")
        assert response.status_code == 503
        assert "private-" not in response.text
