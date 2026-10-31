"""B09 full catalogs, SQL merge and explicit offline behavior."""
from types import SimpleNamespace

import pytest

from data_sync_etl.main import create_app
from data_sync_etl.ports.source_catalogs import SourceCatalogError
from tests.local_admin_client import TestClient


def source_rows(n=1100):
    return [{"id": f"unused-{i:04}", "name": f"Đơn vị {i:04}",
             "provider_type": "PUBLISHER", "available": True} for i in range(n)]


@pytest.fixture
def catalog(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        template = repo.get("master_learning_resources", "resource-1")
        repo.insert("master_learning_resources", {**template, "id": "local-only",
            "mongo_id": None, "source_origin": "LOCAL", "provider_id": "local-provider",
            "provider_name": "Đơn vị LOCAL", "provider_type": "INTERNAL",
            "resource_type_id": "local-type", "resource_type_code": "LOCAL_TYPE",
            "resource_type_name_vi": "Local type"})
    return container


def test_full_source_unused_catalog_count_page_unicode(catalog):
    c = catalog
    rows = source_rows()
    c.resource_lookups.source = SimpleNamespace(catalog_rows=lambda kind: rows)
    result = c.resource_lookups.list_providers(page=1, size=7, query="ĐƠN VỊ")
    assert result["catalog"]["completeness"] == "ALL_SOURCE_PLUS_LOCAL"
    assert result["pagination"] == {"page": 1, "size": 7, "total": 1101, "total_pages": 158}
    assert result["items"][0]["id"] == "unused-0000"
    last = c.resource_lookups.list_providers(page=158, size=7, query="Đơn vị")
    assert [r["id"] for r in last["items"]] == ["unused-1099", "local-provider"]
    out = c.resource_lookups.list_providers(page=999, size=7, query=None)
    assert out["items"] == [] and out["pagination"]["total"] == 1101
    with pytest.raises(ValueError):
        c.resource_lookups.list_providers(page=1, size=101, query=None)


def test_source_precedence_inactive_deleted_and_same_name(catalog):
    c = catalog
    rows = [{"id": "local-provider", "name": "SOURCE authoritative", "provider_type": "PUBLISHER", "available": True},
            {"id": "another", "name": "SOURCE authoritative", "provider_type": "PUBLISHER", "available": True},
            {"id": "disabled", "name": "Hidden", "provider_type": "INTERNAL", "available": False}]
    c.resource_lookups.source = SimpleNamespace(catalog_rows=lambda kind: rows)
    result = c.resource_lookups.list_providers(page=1, size=100, query=None)
    assert result["pagination"]["total"] == 2
    assert {r["id"] for r in result["items"]} == {"local-provider", "another"}
    assert all(r["name"] == "SOURCE authoritative" for r in result["items"])
    assert result["catalog"]["identity_overlaps"] == 1
    rows[0]["available"] = False
    assert [r["id"] for r in c.resource_lookups.list_providers(page=1, size=100, query=None)["items"]] == ["another"]


def test_types_unused_and_local_and_real_code_collision(catalog):
    c = catalog
    rows = [{"id": "source-type", "code": "UNUSED", "name": "Chưa dùng", "available": True},
            {"id": "other-id", "code": "LOCAL_TYPE", "name": "SOURCE name", "available": True}]
    c.resource_lookups.source = SimpleNamespace(catalog_rows=lambda kind: rows)
    result = c.resource_lookups.list_resource_types(page=1, size=100, query=None)
    assert {r["code"] for r in result["items"]} == {"UNUSED", "LOCAL_TYPE"}
    assert next(r["name"] for r in result["items"] if r["code"] == "LOCAL_TYPE") == "SOURCE name"
    assert result["catalog"]["identity_overlaps"] == 1
    assert c.resource_lookups.list_resource_types(page=1, size=7, query="CHƯA")["pagination"]["total"] == 1


def test_offline_empty_and_recovery_are_distinct(catalog):
    c = catalog
    c.resource_lookups.source = None
    disabled = c.resource_lookups.list_providers(page=1, size=100, query=None)
    assert disabled["catalog"]["source_status"] == "SOURCE_DISABLED"
    assert disabled["catalog"]["completeness"] == "PARTIAL_FALLBACK"
    def unavailable(kind):
        raise SourceCatalogError("SOURCE_UNAVAILABLE")
    c.resource_lookups.source = SimpleNamespace(catalog_rows=unavailable)
    offline = c.resource_lookups.list_providers(page=1, size=100, query=None)
    assert offline["items"] == disabled["items"] and offline["catalog"]["source_status"] == "SOURCE_UNAVAILABLE"
    c.resource_lookups.source.catalog_rows = lambda kind: []
    empty = c.resource_lookups.list_providers(page=1, size=100, query=None)
    assert empty["catalog"]["source_status"] == "SOURCE_EMPTY"
    assert [r["id"] for r in empty["items"]] == ["local-provider"]
    c.resource_lookups.source.catalog_rows = lambda kind: source_rows(1)
    recovered = c.resource_lookups.list_providers(page=1, size=100, query=None)
    assert recovered["catalog"]["source_status"] == "SOURCE_OK"
    assert {r["id"] for r in recovered["items"]} == {"unused-0000", "local-provider"}


def test_catalog_api_additive_legacy_rows_and_sanitized_error(catalog):
    c = catalog
    c.resource_lookups.source = SimpleNamespace(catalog_rows=lambda kind: source_rows(1))
    with TestClient(create_app(c)) as client:
        response = client.get("/api/v1/providers")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"items", "pagination", "catalog"}
        assert set(body["items"][0]) == {"id", "name", "provider_type"}
        assert body["catalog"]["completeness"] == "ALL_SOURCE_PLUS_LOCAL"
        def bad(kind):
            raise SourceCatalogError("SOURCE_ERROR")
        c.resource_lookups.source.catalog_rows = bad
        response = client.get("/api/v1/providers")
        assert response.status_code == 503 and response.json()["code"] == "SOURCE_ERROR"
        assert "postgresql" not in response.text
        assert client.get("/api/v1/providers?size=101").status_code == 422


def test_empty_fallback_is_not_verified_empty_source(container):
    result = container.resource_lookups.list_providers(page=1, size=7, query=None)
    assert result["items"] == [] and result["catalog"]["completeness"] == "PARTIAL_FALLBACK"


@pytest.mark.parametrize("state,exception,status", [
    ("08006", "operational", "SOURCE_UNAVAILABLE"),
    ("57014", "operational", "SOURCE_UNAVAILABLE"),
    (None, "timeout", "SOURCE_UNAVAILABLE"),
    ("28P01", "operational", "SOURCE_ERROR"),
    ("42501", "programming", "SOURCE_ERROR"),
    ("42P01", "programming", "SOURCE_ERROR"),
])
def test_source_error_classification_is_sanitized(state, exception, status):
    from sqlalchemy.exc import OperationalError, ProgrammingError, TimeoutError

    from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
    orig = Exception("PRIVATE credentials SQL")
    orig.sqlstate = state
    error = TimeoutError("PRIVATE") if exception == "timeout" else (
        OperationalError if exception == "operational" else ProgrammingError)("PRIVATE SQL", {}, orig)
    def connect():
        raise error
    adapter = object.__new__(PostgreSQLSourceAdapter)
    adapter.engine = SimpleNamespace(connect=connect)
    with pytest.raises(SourceCatalogError) as caught:
        adapter.catalog_rows("providers")
    assert caught.value.status == status
    assert "PRIVATE" not in str(caught.value)


def test_catalog_support_preserves_shared_source_engine_pool_configuration(monkeypatch):
    from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter

    calls = []
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def create(url, **kwargs):
        calls.append(kwargs)
        return engine

    monkeypatch.setattr("data_sync_etl.adapters.source.postgresql.create_engine", create)
    PostgreSQLSourceAdapter("postgresql+psycopg://127.0.0.1/etl_test_b04_b09")
    assert len(calls) == 1
    assert calls[0]["connect_args"] == {"connect_timeout": 15}
    assert "pool_timeout" not in calls[0]


def test_catalog_change_between_requests_is_eventually_consistent(catalog):
    rows = source_rows(8)
    catalog.resource_lookups.source = SimpleNamespace(catalog_rows=lambda kind: list(rows))
    first = catalog.resource_lookups.list_providers(page=1, size=7, query=None)
    assert first["pagination"]["total"] == 9
    rows.pop()
    last = catalog.resource_lookups.list_providers(page=2, size=7, query=None)
    assert last["pagination"]["total"] == 8 and len(last["items"]) == 1
    assert last["items"][0]["id"] == "local-provider"



def test_full_source_options_create_and_update_local_without_rewriting_history(catalog):
    from tests.unit.test_resource_lookups import pdf_bytes
    c = catalog
    providers = [
        {"id": "provider-1", "name": "SOURCE renamed provider", "provider_type": "PUBLISHER", "available": True},
        {"id": "unused-provider", "name": "Unused SOURCE provider", "provider_type": "INTERNAL", "available": True},
    ]
    types = [{"id": "real-source-type-id", "code": "UNUSED_SOURCE", "name": "Unused SOURCE type", "available": True}]
    c.resource_lookups.source = SimpleNamespace(catalog_rows=lambda kind: providers if kind == "providers" else types)
    c.settings.admin_enabled = True
    with c.uow() as repo:
        before = repo.get("master_learning_resources", "resource-1")
    with TestClient(create_app(c)) as client:
        created = client.post("/api/v1/resources", data={
            "title": "Created from authoritative SOURCE selection", "resource_type_code": "UNUSED_SOURCE",
            "resource_type_name_vi": "Unused SOURCE type", "provider_id": "provider-1",
            "provider_name": "SOURCE renamed provider", "provider_type": "PUBLISHER",
        }, files={"file": ("source-options.pdf", pdf_bytes(), "application/pdf")})
        assert created.status_code == 201, created.text
        updated = client.patch("/api/v1/resources/local-only/metadata", json={
            "provider_id": "unused-provider", "provider_name": "Unused SOURCE provider", "provider_type": "INTERNAL",
            "resource_type_code": "UNUSED_SOURCE", "resource_type_name_vi": "Unused SOURCE type",
        })
        assert updated.status_code == 200, updated.text
        assert client.patch("/api/v1/resources/local-only/metadata", json={"resource_type_id": "untrusted-id"}).status_code == 422
    with c.uow() as repo:
        created_row = repo.get("master_learning_resources", created.json()["id"])
        updated_row = repo.get("master_learning_resources", "local-only")
        assert created_row["source_origin"] == updated_row["source_origin"] == "LOCAL"
        assert created_row["resource_type_id"] == updated_row["resource_type_id"] == "real-source-type-id"
        assert created_row["provider_name"] == "SOURCE renamed provider"
        assert updated_row["provider_id"] == "unused-provider"
        assert repo.get("master_learning_resources", "resource-1") == before


def test_inactive_source_choice_and_source_owned_patch_stay_blocked(catalog):
    from tests.unit.test_resource_lookups import pdf_bytes
    c = catalog
    c.resource_lookups.source = SimpleNamespace(catalog_rows=lambda kind: [
        {"id": "provider-1", "name": "Disabled", "provider_type": "INTERNAL", "available": False}
    ] if kind == "providers" else [])
    c.settings.admin_enabled = True
    with TestClient(create_app(c)) as client:
        rejected = client.post("/api/v1/resources", data={
            "title": "Must reject inactive", "resource_type_code": "HANDOUT", "provider_id": "provider-1",
            "provider_name": "Disabled", "provider_type": "INTERNAL",
        }, files={"file": ("invalid-options.pdf", pdf_bytes(), "application/pdf")})
        assert rejected.status_code == 400, rejected.text
        rejected = client.patch("/api/v1/resources/local-only/metadata", json={"provider_id": "provider-1"})
        assert rejected.status_code == 400, rejected.text
        with c.uow() as repo:
            repo.update("master_learning_resources", "resource-1", {"source_origin": "POSTGRESQL"})
        def no_source_rpc(kind):
            pytest.fail("ownership must reject before SOURCE lookup")
        c.resource_lookups.source.catalog_rows = no_source_rpc
        assert client.patch("/api/v1/resources/resource-1/metadata", json={"provider_id": "new-id"}).status_code == 409
