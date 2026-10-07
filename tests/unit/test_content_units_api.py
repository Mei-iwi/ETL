from conftest import completed
from fastapi.testclient import TestClient

from data_sync_etl.main import create_app


def test_content_units_read_pagination_search_and_version_isolation(container):
    version, _ = completed(container)
    container.postprocess.run(version["id"])
    other = container.ingestion.run("resource-2", "input", "sample.pdf")["version"]
    container.ocr.create_job(other["id"])
    while container.worker.once("test-worker"):
        pass
    container.postprocess.run(other["id"])
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
        assert repo.count("etl_stage_runs", {}) == 2


def test_content_units_admin_validation_and_empty_version(container):
    version, _ = completed(container)
    path = f"/admin/resource-versions/{version['id']}/content-units"
    with TestClient(create_app(container)) as client:
        assert client.get(path).status_code == 403
        container.settings.admin_enabled = True
        assert client.get(path).json() == {"items": [], "total": 0, "offset": 0, "limit": 50}
        assert client.get("/admin/resource-versions/missing/content-units").status_code == 400
        for params in ({"offset": -1}, {"limit": 0}, {"limit": 101}, {"search": "x" * 201}):
            assert client.get(path, params=params).status_code == 422
