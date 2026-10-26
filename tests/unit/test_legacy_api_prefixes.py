"""Canonical v1 URLs work; retired aliases never dispatch business actions."""
import pytest
from fastapi.testclient import TestClient as RawClient

from data_sync_etl.api.admin_access import protected
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient

OPERATIONS = [
    ("POST", "/resources/r/versions/ingest", {"bucket": "input", "object_key": "sample.pdf"}, "ingestion", "run"),
    ("POST", "/resource-versions/v/ocr-jobs", {}, "ocr", "create_job"),
    ("POST", "/resource-versions/v/postprocess", None, "postprocess", "run"),
    ("GET", "/resource-versions/v/etl-status", None, "orchestrator", "status"),
]


@pytest.mark.parametrize("method,suffix,body,service,operation", OPERATIONS)
def test_v1_dispatches_and_retired_alias_is_not_registered(
    container, monkeypatch, method, suffix, body, service, operation
):
    container.settings.admin_enabled = True
    calls = []

    def perform(*args, **kwargs):
        calls.append((args, kwargs))
        return {"result": "same-handler"}

    monkeypatch.setattr(getattr(container, service), operation, perform)
    with TestClient(create_app(container)) as api:
        response = api.request(method, '/api/v1' + suffix, **({"json": body} if body is not None else {}))
        assert response.status_code == 200
        assert response.json() == {"result": "same-handler"}
        assert api.request(method, '/admin' + suffix, **({"json": body} if body is not None else {})).status_code == 404
    assert len(calls) == 1
    if service == "ingestion":
        assert calls[0][1] == {"local_only": True}


@pytest.mark.parametrize("prefix", ["/api/v1", "/admin"])
@pytest.mark.parametrize("method,suffix", [(item[0], item[1]) for item in OPERATIONS] + [
    ("GET", "/resource-versions/v/content-units"),
])
def test_new_and_legacy_paths_deny_before_body_when_admin_disabled(container, prefix, method, suffix):
    path = prefix + suffix
    assert protected(path, method)
    with RawClient(create_app(container), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as api:
        response = api.request(method, path, content=b"unparsed-private-body")
    assert response.status_code == 403
    assert response.headers["cache-control"] == "private, no-store"
    assert "unparsed-private-body" not in response.text
