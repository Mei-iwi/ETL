import json

from fastapi import FastAPI
from fastapi.routing import APIRoute

from data_sync_etl.api.routes import router
from scripts.audit_api_hardening import ROOT, inventory


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
