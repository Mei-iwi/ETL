from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.api.admin_access import LocalAdminBoundary, protected
from data_sync_etl.application.create_resource_version import InvalidResourceVersion
from data_sync_etl.domain.resource_version import CreateResourceVersionCommand
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


def client(c, **kwargs):
    return TestClient(
        create_app(c), base_url="http://127.0.0.1", client=("127.0.0.1", 50000), **kwargs
    )


@pytest.mark.parametrize(
    "method,path",
    [
        ("PATCH", "/api/v1/resources/r/metadata"),
        ("POST", "/api/v1/resource/r/versions"),
        ("POST", "/api/v1/resources"),
        ("POST", "/api/v1/resource-versions/v/process"),
        ("POST", "/api/v1/resource-versions/v/index"),
        ("POST", "/api/v1/resource-versions/v/reindex"),
        ("POST", "/admin/sync/master"),
        ("POST", "/admin/resources/r/versions/ingest"),
        ("GET", "/admin/resource-versions/v/content-units"),
        ("GET", "/api/v1/resource-versions/v/content-units"),
        ("GET", "/api/v1/resource-versions/v/chunks"),
        ("GET", "/api/v1/master-data/sync/status"),
        ("GET", "/api/v1/resources/r/versions"),
        ("GET", "/api/v1/processing-jobs/j"),
        ("GET", "/api/v1/resource-versions/v/index-status"),
    ],
)
@pytest.mark.parametrize(
    "bad", ["remote", "disabled", "production", "proxy", "host", "origin", "unknown_launch"]
)
def test_sensitive_routes_deny_before_body_or_service(searchable, method, path, bad, monkeypatch):
    c, _ = searchable
    headers = {"X-Role": "admin", "X-Admin": "true"}
    if bad == "disabled":
        c.settings.admin_enabled = False
    if bad == "production":
        c.settings.app_environment = "production"
    if bad == "proxy":
        headers["Forwarded"] = "for=127.0.0.1"
    if bad == "origin":
        headers["Origin"] = "https://attacker.invalid"
    if bad == "unknown_launch":
        monkeypatch.setattr("sys.argv", [])
    with TestClient(
        create_app(c),
        base_url="http://attacker.invalid" if bad == "host" else "http://127.0.0.1",
        client=("198.51.100.1" if bad == "remote" else "127.0.0.1", 50000),
    ) as api:
        response = api.request(method, path, headers=headers, content=b"private-unparsed-body")
    assert response.status_code == 403
    assert "private-" not in response.text
    assert response.headers["cache-control"] == "private, no-store"


def test_early_asgi_guard_never_receives_multipart(searchable):
    import asyncio

    c, _ = searchable
    c.settings.admin_enabled = False
    calls = []

    async def downstream(*args):
        calls.append("app")

    async def receive():
        calls.append("receive")
        raise AssertionError("Body must not be consumed")

    async def send(message):
        calls.append(message["type"])

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/resource/r/versions",
        "headers": [(b"host", b"127.0.0.1")],
        "scheme": "http",
        "client": ("127.0.0.1", 1),
        "server": ("127.0.0.1", 8000),
        "app": SimpleNamespace(state=SimpleNamespace(container=c)),
    }
    asyncio.run(LocalAdminBoundary(downstream, c.settings)(scope, receive, send))
    assert calls == ["http.response.start", "http.response.body"]


@pytest.mark.parametrize("suffix", ["chunks", "content-units"])
@pytest.mark.parametrize("restriction", ["none", "private", "rejected", "deleted", "public"])
def test_extracted_content_obeys_same_whole_file_policy(searchable, suffix, restriction):
    c, version = searchable
    with c.uow() as repo:
        if restriction in {"private", "deleted"}:
            repo.update(
                "master_learning_resources",
                version["resource_id"],
                {"publication_status": "DRAFT"}
                if restriction == "private"
                else {"is_active": False},
            )
        if restriction == "rejected":
            unit = repo.find("content_units", {"resource_version_id": version["id"]})[0]
            repo.update("content_units", unit["id"], {"review_status": "REJECTED"})
    if restriction == "public":
        c.settings.search_internal_dev_enabled = False
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/{suffix}")
    assert response.status_code == (200 if restriction == "none" else 404)
    assert response.headers["cache-control"] == "private, no-store"
    if restriction != "none":
        assert "Page one" not in response.text


@pytest.mark.parametrize("origin", ["POSTGRESQL", "UNKNOWN", None])
def test_version_ownership_denied_without_reading_or_storing(searchable, origin):
    c, version = searchable
    with c.uow() as repo:
        repo.update(
            "master_learning_resources",
            version["resource_id"],
            {"source_origin": origin or "UNKNOWN"},
        )

    class Unreadable(BytesIO):
        def read(self, *args):
            raise AssertionError("Denied before reading")

    command = CreateResourceVersionCommand(
        version["resource_id"], "a.pdf", Unreadable(b"blocked"), 7, "application/pdf"
    )
    with pytest.raises(InvalidResourceVersion) as error:
        c.resource_version_create.execute(command)
    assert error.value.status_code == 409


def test_local_write_succeeds_but_actor_cannot_be_forged(searchable):
    c, version = searchable
    with c.uow() as repo:
        repo.update("master_learning_resources", version["resource_id"], {"source_origin": "LOCAL"})
    with client(c) as api:
        path = f"/api/v1/resources/{version['resource_id']}/metadata"
        assert api.patch(path, json={"title": "Local changed"}).status_code == 200
        for actor in ("admin", "", None):
            assert api.patch(path, json={"uploaded_by": actor}).status_code == 400
        assert (
            api.post(
                f"/api/v1/resource/{version['resource_id']}/versions",
                files={"file": ("x.pdf", b"%PDF-forged", "application/pdf")},
                data={"uploaded_by": "admin"},
            ).status_code
            == 400
        )


def test_route_classification_does_not_turn_search_into_admin():
    assert not protected("/api/v1/search", "POST")
    assert not protected("/health", "GET")
    assert protected("/admin/resource-versions/v/etl-status", "GET")


@pytest.mark.parametrize("origin", ["POSTGRESQL", "UNKNOWN", "MISSING", "LOCAL"])
def test_ingest_cannot_bypass_local_ownership(searchable, monkeypatch, origin):
    from conftest import make_pdf

    c, version = searchable
    resource_id = "missing-resource" if origin == "MISSING" else version["resource_id"]
    if origin != "MISSING":
        with c.uow() as repo:
            repo.update("master_learning_resources", resource_id, {"source_origin": origin})
    make_pdf(c, ["Legacy local upload"], "legacy.pdf")
    calls = []
    original = c.storage.snapshot

    def snapshot(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(c.storage, "snapshot", snapshot)
    with client(c) as api:
        response = api.post(f"/api/v1/resources/{resource_id}/versions/ingest",
                            json={"bucket": "input", "object_key": "legacy.pdf"})
    assert response.status_code == (200 if origin == "LOCAL" else 404 if origin == "MISSING" else 409)
    assert len(calls) == (1 if origin == "LOCAL" else 0)


def test_version_upload_uses_request_body_limit(searchable):
    c, version = searchable
    c.settings.upload_request_max_bytes = 1024
    with client(c) as api:
        response = api.post(f"/api/v1/resource/{version['resource_id']}/versions",
                            content=b"not parsed", headers={"Content-Length": "1025"})
    assert response.status_code == 413


def test_failed_version_write_does_not_unlink_shared_published_bytes(searchable, monkeypatch):
    from conftest import make_pdf

    from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
    from data_sync_etl.application.create_resource_version import ResourceVersionPersistenceError

    c, version = searchable
    with c.uow() as repo:
        repo.update("master_learning_resources", version["resource_id"], {"source_origin": "LOCAL"})
    path = make_pdf(c, ["New upload bytes"], "new-upload.pdf")
    original_save = c.storage.save_stream
    original_insert = SQLAlchemyRepository.insert
    saved = []

    def publish(*args, **kwargs):
        stored = original_save(*args, **kwargs)
        saved.append(stored)
        return stored

    def reject(repo, table, values):
        if table == "resource_versions":
            raise RuntimeError("PRIVATE_DATABASE_FAILURE")
        return original_insert(repo, table, values)

    monkeypatch.setattr(c.storage, "save_stream", publish)
    monkeypatch.setattr(SQLAlchemyRepository, "insert", reject)
    with path.open("rb") as stream, pytest.raises(ResourceVersionPersistenceError):
        c.resource_version_create.execute(
            CreateResourceVersionCommand(
                version["resource_id"], "new.pdf", stream, path.stat().st_size, "application/pdf"
            )
        )
    assert len(saved) == 1
    assert c.storage.exists(saved[0].bucket, saved[0].object_key)
