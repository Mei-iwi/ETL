import pytest

from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize("searchable", [{"unreviewed": True}], indirect=True)
def test_content_units_read_pagination_search_and_version_isolation(searchable):
    container, version = searchable
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-2', {'source_origin': 'LOCAL'})
    other = container.ingestion.run("resource-2", "input", "sample.pdf")["version"]
    container.ocr.create_job(other["id"])
    while container.worker.once("test-worker"):
        pass
    container.settings.admin_enabled = True
    path = f"/admin/resource-versions/{version['id']}/content-units"
    with TestClient(create_app(container)) as client:
        first = client.get(path, params={"limit": 2})
        assert first.status_code == 200
        assert first.headers["X-Correlation-ID"]
        data = first.json()
        assert data["total"] == 3 and data["offset"] == 0 and data["limit"] == 2
        assert [item["sequence_no"] for item in data["items"]] == [1, 2]
        assert all(item["resource_version_id"] == version["id"] for item in data["items"])
        assert data["items"][0]["text"] == "Page one text"
        assert data["items"][0]["page_result_id"]
        assert data["items"][0]["retrieval_eligible"] is False
        last = client.get(path, params={"offset": 2, "limit": 2}).json()
        assert [item["sequence_no"] for item in last["items"]] == [3]
        assert not {i["id"] for i in data["items"]} & {i["id"] for i in last["items"]}
        match = client.get(path, params={"search": "PAGE TWO"}).json()
        assert match["total"] == 1 and match["items"][0]["sequence_no"] == 2
        assert client.get(path, params={"search": data["items"][0]["id"]}).json()["total"] == 1
        # LIKE wildcards must be treated as literal search characters.
        assert client.get(path, params={"search": "%"}).json()["total"] == 0
        assert client.get(path, params={"offset": 10}).json()["items"] == []
    with container.uow() as repo:
        assert repo.count("content_units", {}) == 6
        assert repo.count("etl_stage_runs", {"stage": "POSTPROCESS"}) == 2
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 2


def test_content_units_admin_validation_and_empty_version(searchable):
    container, _ = searchable
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-2', {'source_origin': 'LOCAL'})
    version = container.ingestion.run("resource-2", "input", "sample.pdf")["version"]
    path = f"/admin/resource-versions/{version['id']}/content-units"
    container.settings.admin_enabled = False
    with TestClient(create_app(container)) as client:
        assert client.get(path).status_code == 403
        container.settings.admin_enabled = True
        assert client.get(path).status_code == 409
        assert client.get("/admin/resource-versions/missing/content-units").status_code == 404
        for params in ({"offset": -1}, {"limit": 0}, {"limit": 101}, {"search": "x" * 201}):
            assert client.get(path, params=params).status_code == 422
