import io

from fastapi.testclient import TestClient

from data_sync_etl.main import create_app


def test_create_resource_creates_initial_version(container):
    payload = b"%PDF-1.7\nminimal test PDF payload"
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "New learning resource",
                "resource_type_code": "HANDOUT",
                "provider_id": "provider-test",
                "provider_name": "Test provider",
            },
            files={"file": ("sample.pdf", io.BytesIO(payload), "application/pdf")},
        )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "New learning resource"
    assert body["version_number"] == 1
    assert body["lifecycle_status"] == "INGESTED"
    assert len(body["source_hash"]) == 64
    with container.uow() as repo:
        resource = repo.get("master_learning_resources", body["id"])
        version = repo.get("resource_versions", body["initial_version_id"])
    assert resource["title"] == body["title"]
    assert version["resource_id"] == body["id"]
    assert version["source_hash"] == body["source_hash"]
    assert container.storage.exists(version["storage_bucket"], version["storage_object_key"])


def test_create_resource_rejects_mismatched_signature(container):
    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "Invalid resource",
                "resource_type_code": "HANDOUT",
                "provider_id": "provider-test",
                "provider_name": "Test provider",
            },
            files={"file": ("sample.pdf", io.BytesIO(b"not a pdf"), "application/pdf")},
        )
    assert response.status_code == 400
    assert "not a pdf" not in response.text
    with container.uow() as repo:
        assert repo.count("master_learning_resources", {"title": "Invalid resource"}) == 0
