from conftest import completed, ingest
from fastapi.testclient import TestClient

from data_sync_etl.main import create_app


def test_list_content_units_pagination_and_search(container):
    version, _ = completed(container)
    path = f"/api/v1/resource-versions/{version['id']}/content-units"

    # API đọc content units dùng được khi admin API đang tắt.
    assert container.settings.admin_enabled is False

    with TestClient(create_app(container)) as client:
        first = client.get(path, params={"offset": 0, "limit": 2})
        last = client.get(path, params={"offset": 2, "limit": 2})
        match = client.get(path, params={"search": "PAGE TWO"})

    assert first.status_code == 200
    data = first.json()
    assert data["total"] == 3
    assert data["offset"] == 0
    assert data["limit"] == 2
    assert [item["sequence_no"] for item in data["items"]] == [1, 2]
    assert all(item["resource_version_id"] == version["id"] for item in data["items"])

    assert last.status_code == 200
    assert [item["sequence_no"] for item in last.json()["items"]] == [3]

    assert match.status_code == 200
    assert match.json()["total"] == 1
    assert match.json()["items"][0]["sequence_no"] == 2


def test_content_units_empty_before_processing(container):
    version = ingest(container)
    path = f"/api/v1/resource-versions/{version['id']}/content-units"

    with TestClient(create_app(container)) as client:
        response = client.get(path)

    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["total"] == 0


def test_content_units_version_not_found(container):
    with TestClient(create_app(container)) as client:
        response = client.get(
            "/api/v1/resource-versions/not-found/content-units"
        )

    assert response.status_code == 404


def test_content_units_rejects_invalid_query_parameters(container):
    version, _ = completed(container)
    path = f"/api/v1/resource-versions/{version['id']}/content-units"

    with TestClient(create_app(container)) as client:
        assert client.get(path, params={"offset": -1}).status_code == 422
        assert client.get(path, params={"limit": 0}).status_code == 422
        assert client.get(path, params={"limit": 101}).status_code == 422
        assert client.get(path, params={"search": "x" * 201}).status_code == 422