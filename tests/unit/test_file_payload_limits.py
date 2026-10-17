from types import SimpleNamespace

from fastapi.testclient import TestClient

from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


def api(c):
    return TestClient(create_app(c), base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def test_server_file_limit_applies_before_range_and_open(searchable, monkeypatch):
    c, version = searchable
    class Oversized:
        def stat(self):
            return SimpleNamespace(st_size=51 * 1024 * 1024)
        def open(self, *args):
            raise AssertionError("Oversized file must not be read")
    monkeypatch.setattr(c.storage, "get_local_path", lambda *args: Oversized())
    monkeypatch.setattr(c.storage, "exists", lambda *args: True)
    with api(c) as client:
        response = client.get(f"/api/v1/resource-versions/{version['id']}/file",
                              headers={"Range": "bytes=0-4"})
    assert response.status_code == 413
    assert "storage" not in response.text


def test_file_backend_error_is_safe_502(searchable, monkeypatch):
    c, version = searchable
    def unavailable(*args):
        raise RuntimeError("PRIVATE_STORAGE_CREDENTIAL")
    monkeypatch.setattr(c.storage, "get_local_path", unavailable)
    monkeypatch.setattr(c.storage, "exists", lambda *args: True)
    with api(c) as client:
        response = client.get(f"/api/v1/resource-versions/{version['id']}/file")
    assert response.status_code == 502 and "PRIVATE" not in response.text
