import pytest
from conftest import ingest, make_pdf

from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


@pytest.fixture(autouse=True)
def privileged_local_read(container):
    container.settings.admin_enabled = True


def test_resource_version_history_returns_newest_first(container):
    first_version = ingest(container)
    make_pdf(container, texts=["Updated learning material"], name="updated.pdf")
    second_version = container.ingestion.run(
        "resource-1", "input", "updated.pdf"
    )["version"]

    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/resources/resource-1/versions")

    assert response.status_code == 200
    versions = response.json()
    assert [version["version_number"] for version in versions] == [2, 1]
    assert [version["id"] for version in versions] == [
        second_version["id"],
        first_version["id"],
    ]
    assert versions[0]["parent_version_id"] == first_version["id"]
    assert versions[1]["parent_version_id"] is None
    assert all(version["lifecycle_status"] == "INGESTED" for version in versions)

    # Storage paths and content hashes are internal metadata.
    assert "storage_bucket" not in response.text
    assert "storage_object_key" not in response.text
    assert "source_hash" not in response.text


def test_resource_version_history_is_empty_when_resource_has_no_versions(container):
    container.sync.run(full=True)

    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/resources/resource-2/versions")

    assert response.status_code == 200
    assert response.json() == []


def test_resource_version_history_resource_not_found(container):
    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/resources/not-found/versions")

    assert response.status_code == 404
    assert response.json()["detail"] == "Resource not found"


def test_resource_version_history_rejects_invalid_resource_id(container):
    with TestClient(create_app(container)) as client:
        response = client.get(f"/api/v1/resources/{'x' * 31}/versions")

    assert response.status_code == 422
