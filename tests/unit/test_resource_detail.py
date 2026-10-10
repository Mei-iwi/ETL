from fastapi.testclient import TestClient

from data_sync_etl.domain.core import now
from data_sync_etl.main import create_app


def test_resource_detail_success(container):
    container.sync.run(full=True)

    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/resources/resource-1")

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == "resource-1"
    assert data["title"]
    assert data["provider_id"]
    assert data["provider_name"]
    assert data["provider_type"]
    assert data["resource_type_code"]
    assert data["resource_type_name_vi"]
    assert data["publication_status"]
    assert data["is_active"] is True
    assert "created_at" in data
    assert "updated_at" in data

    # Check relation lists
    assert isinstance(data["subjects"], list)
    assert isinstance(data["grade_levels"], list)

    # Sensitive data exclusion check
    assert "hashed_password" not in response.text


def test_resource_detail_not_found(container):
    container.sync.run(full=True)

    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/resources/non-existent-id")

    assert response.status_code == 404
    assert response.json()["detail"] == "Resource not found"


def test_resource_detail_deleted_excluded(container):
    container.sync.run(full=True)

    with container.uow() as repo:
        repo.update(
            "master_learning_resources",
            "resource-2",
            {"deleted_at": now()},
        )

    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/resources/resource-2")

    assert response.status_code == 404
