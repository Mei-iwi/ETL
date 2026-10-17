"""Canonical v1 URLs retain legacy behavior and access policy."""
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
def test_v1_and_legacy_alias_dispatch_to_same_guarded_service(
    container, monkeypatch, method, suffix, body, service, operation
):
    container.settings.admin_enabled = True
    calls = []

    def perform(*args, **kwargs):
        calls.append((args, kwargs))
        return {"result": "same-handler"}

    monkeypatch.setattr(getattr(container, service), operation, perform)
    with TestClient(create_app(container)) as api:
        for prefix in ("/api/v1", "/admin"):
            response = api.request(method, prefix + suffix, **({"json": body} if body is not None else {}))
            assert response.status_code == 200
            assert response.json() == {"result": "same-handler"}
    assert len(calls) == 2 and calls[0] == calls[1]
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
