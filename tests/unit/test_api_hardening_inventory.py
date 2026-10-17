import json

from fastapi import FastAPI
from fastapi.routing import APIRoute

from data_sync_etl.api.routes import router
from scripts.audit_api_hardening import ROOT, inventory

LEGACY_API_TAGS = {
    ("/admin/resources/{id}/versions/ingest", "post"): "Learning Resource Ingestion and Management",
    ("/admin/resource-versions/{id}/ocr-jobs", "post"): "Multimedia Data Processing and Normalization",
    ("/admin/resource-versions/{id}/postprocess", "post"): "Multimedia Data Processing and Normalization",
    ("/admin/resource-versions/{id}/etl-status", "get"): "Multimedia Data Processing and Normalization",
    ("/admin/resource-versions/{id}/content-units", "get"): "Multimedia Data Processing and Normalization",
}


def test_inventory_covers_every_business_method_and_hidden_alias():
    rows, schema = inventory()
    actual = {(r["method"], r["path"]) for r in rows}
    expected = {(m, r.path) for r in router.routes if isinstance(r, APIRoute) for m in r.methods}
    assert expected <= actual
    assert len(actual) == len(rows)
    assert ("POST", "/admin/sync/master") in actual
    app = FastAPI()
    app.include_router(router)
    assert schema["paths"] == app.openapi()["paths"]
    assert all(r["decision"] == "KEEP" for r in rows)
    assert any(r["frontend_calls"] for r in rows)
    assert any(r["test_script_calls"] for r in rows)


def test_schema_baseline_and_register_do_not_approve_pending_schema():
    schema = json.loads((ROOT / "docs/audits/erd-contract/contract_snapshot.json").read_text(encoding="utf-8"))
    assert [len(schema["source"]), len(schema["master"]), len(schema["mongo"]["validators"])] == [15, 18, 5]
    register = json.loads((ROOT / "docs/audits/master-migration/decisions.json").read_text(encoding="utf-8"))
    assert register["approved_schema_changes"] == []
    assert register["schema_policy"] == "PRESERVE"


def test_openapi_keep_routes_and_models_match_g0_baseline():
    _, current = inventory()
    before = json.loads((ROOT / "docs/audits/api-hardening/openapi_before.json").read_text(encoding="utf-8"))
    # Keep the snapshot frozen; account only for approved prefix/tag changes.
    for (path, method), tag in LEGACY_API_TAGS.items():
        new_path = path.replace("/admin/", "/api/v1/", 1)
        old_operations = before["paths"].pop(path)
        assert path not in current["paths"]
        assert current["paths"][new_path][method]["tags"] == [tag]
        if new_path not in before["paths"]:
            old_operations[method]["tags"] = [tag]
            old_operations[method]["operationId"] = current["paths"][new_path][method]["operationId"]
            before["paths"][new_path] = old_operations
    assert current["paths"] == before["paths"]
    assert current["components"] == before["components"]


def test_all_documented_business_operations_have_explicit_non_default_tags():
    _, schema = inventory()
    for operations in schema["paths"].values():
        for method, operation in operations.items():
            if method in {"get", "post", "put", "patch", "delete", "head", "options"}:
                assert operation.get("tags")
                assert "default" not in operation["tags"]
    assert "/admin/sync/master" not in schema["paths"]
