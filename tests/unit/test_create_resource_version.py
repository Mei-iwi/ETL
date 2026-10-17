from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def test_create_resource_version_route(container, pdf_file):
    container.sync.run(full=True)
    container.settings.admin_enabled = True
    with container.uow() as repo:
        repo.update("master_learning_resources", "resource-1", {"source_origin": "LOCAL"})
    payload = pdf_file.read_bytes()

    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resource/resource-1/versions",
            files={"file": ("version.pdf", payload, "application/pdf")},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["action"] == "CREATED"
        assert body["resource_id"] == "resource-1"
        assert body["version_number"] == 1
        assert body["parent_version_id"] is None
        assert body["lifecycle_status"] == "INGESTED"

        duplicate = client.post(
            "/api/v1/resource/resource-1/versions",
            files={"file": ("version.pdf", payload, "application/pdf")},
        )

    assert duplicate.status_code == 200
    assert duplicate.json()["action"] == "NO_CHANGE"
    assert duplicate.json()["resource_version_id"] == body["resource_version_id"]


def test_create_resource_version_rejects_unknown_resource(container, pdf_file):
    container.settings.admin_enabled = True
    payload = pdf_file.read_bytes()
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resource/missing-resource/versions",
            files={"file": ("version.pdf", payload, "application/pdf")},
        )
    assert response.status_code == 404
