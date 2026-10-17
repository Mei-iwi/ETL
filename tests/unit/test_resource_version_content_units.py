from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient
from tests.unit.test_search import searchable as searchable


def test_list_content_units_pagination_and_search(searchable):
    container, version = searchable
    path = f"/api/v1/resource-versions/{version['id']}/content-units"

    container.settings.admin_enabled = False
    with TestClient(create_app(container)) as denied:
        assert denied.get(path).status_code == 403
    container.settings.admin_enabled = True

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


def test_content_units_empty_before_processing(searchable):
    container, _ = searchable
    version = container.ingestion.run("resource-2", "input", "sample.pdf")["version"]
    path = f"/api/v1/resource-versions/{version['id']}/content-units"

    with TestClient(create_app(container)) as client:
        response = client.get(path)

    assert response.status_code == 409
    assert "items" not in response.json()


def test_content_units_version_not_found(searchable):
    container, _ = searchable
    with TestClient(create_app(container)) as client:
        response = client.get(
            "/api/v1/resource-versions/not-found/content-units"
        )

    assert response.status_code == 404


def test_content_units_rejects_invalid_query_parameters(searchable):
    container, version = searchable
    path = f"/api/v1/resource-versions/{version['id']}/content-units"

    with TestClient(create_app(container)) as client:
        assert client.get(path, params={"offset": -1}).status_code == 422
        assert client.get(path, params={"limit": 0}).status_code == 422
        assert client.get(path, params={"limit": 101}).status_code == 422
        assert client.get(path, params={"search": "x" * 201}).status_code == 422
