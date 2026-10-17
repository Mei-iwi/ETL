"""Offline API inventory. No app startup, dotenv, credentials, DB or network."""

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

from data_sync_etl.api.routes import router
from scripts.audit_erd_contract import collect, readonly_guard

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/audits/api-hardening"
HISTORY = "1135ccd"
SCHEMA_KEYS = (
    "references",
    "implementation_sha256",
    "source",
    "source_enums",
    "master",
    "mongo",
    "chains",
)


def inventory():
    app = FastAPI(title="Data Sync + ETL (local admin/dev)")
    app.include_router(router)
    # main.py adds middleware/lifespan, no additional business routes.
    source = (ROOT / "src/data_sync_etl/api/routes.py").read_text(encoding="utf-8")
    nodes = {
        n.name: n
        for n in ast.parse(source).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).splitlines()
    corpus = {}
    for name in files:
        p = ROOT / name
        if (
            (
                name.startswith(("frontend/src/", "tests/", "scripts/", "src/data_sync_etl/"))
                or name == "README.md"
            )
            and p.is_file()
            and p.suffix in {".py", ".ts", ".tsx", ".mjs", ".md"}
        ):
            corpus[name] = p.read_text(encoding="utf-8-sig").splitlines()
    result = []
    for route in router.routes:
        if not isinstance(route, APIRoute):
            continue
        node = nodes[route.endpoint.__name__]
        body = ast.get_source_segment(source, node)
        # Dynamic JS/URL template segments are deliberately broad, not literal-only grep.
        pattern = re.compile(re.sub(r"\\\{[^}]+\\\}", r"[^\\n]*?", re.escape(route.path)))
        hits = []
        for name, lines in corpus.items():
            if name == "src/data_sync_etl/api/routes.py":
                continue
            for line, value in enumerate(lines, 1):
                if pattern.search(value):
                    hits.append(f"{name}:{line}")
        frontend = [h for h in hits if h.startswith("frontend/src/")]
        tests = [h for h in hits if h.startswith(("tests/", "scripts/"))]
        classification = (
            "USED_FRONTEND"
            if frontend
            else "USED_TEST_SCRIPT"
            if tests
            else "EXTERNALLY_EXPOSED_UNKNOWN"
        )
        path = route.path
        category = (
            "FILE_STREAM"
            if path.endswith("/file")
            else "SEARCH_CONTEXT"
            if path.endswith(("/search", "/context"))
            else "READ_SENSITIVE"
            if path.endswith(("/chunks", "/content-units"))
            else "ADMIN_WRITE"
            if set(route.methods) - {"GET", "HEAD"}
            else "READ_PUBLIC"
            if path in {"/health", "/api/v1/system/capabilities"}
            else "ADMIN_READ"
        )
        calls = sorted(
            {
                n.func.id
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
        )
        service_refs = sorted(
            set(
                re.findall(
                    r"(?:container|services\(request\)|request.app.state.container)\.([a-z_]+)",
                    body,
                )
            )
        )
        for method in sorted(route.methods):
            result.append(
                {
                    "method": method,
                    "path": path,
                    "handler": route.endpoint.__name__,
                    "tags": route.tags,
                "request": str(route.body_field.field_info.annotation) if route.body_field else None,
                    "response": str(route.response_model),
                    "success_status": route.status_code or 200,
                    "documented": route.include_in_schema,
                    "category": category,
                    "classification": classification,
                    "decision": "KEEP",
                    "call_sites": hits,
                    "frontend_calls": frontend,
                    "test_script_calls": tests,
                    "service_refs": service_refs,
                    "guard_calls": [
                        x
                        for x in calls
                        if x
                        in {"services", "source_file_admin", "evaluation_services", "search_access"}
                    ],
                    "evidence": f"src/data_sync_etl/api/routes.py:{node.lineno}",
                    "external_compatibility": "UNKNOWN: no owner-approved deprecation contract; no removal authorized",
                }
            )
    for route in app.routes:
        if not getattr(route, "path", None) or isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods):
            result.append(
                {
                    "method": method,
                    "path": route.path,
                    "handler": route.name,
                    "tags": ["Framework"],
                    "request": None,
                    "response": None,
                    "success_status": 200,
                    "documented": False,
                    "category": "READ_PUBLIC",
                    "classification": "EXTERNALLY_EXPOSED_UNKNOWN",
                    "decision": "KEEP",
                    "call_sites": [],
                    "frontend_calls": [],
                    "test_script_calls": [],
                    "service_refs": [],
                    "guard_calls": [],
                    "evidence": "FastAPI framework",
                    "external_compatibility": "Framework documentation, not an unused internal business API",
                }
            )
    assert result and len(app.openapi()["paths"]) > 0
    return result, app.openapi()


def main():
    sys.addaudithook(readonly_guard)
    OUT.mkdir(parents=True, exist_ok=True)
    _, current = collect()
    schema = json.loads(json.dumps(current))
    old = json.loads(
        subprocess.check_output(
            ["git", "show", f"{HISTORY}:docs/audits/erd-contract/contract_snapshot.json"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        )
    )
    assert all(schema[key] == old[key] for key in SCHEMA_KEYS), "Immutable schema/reference drift"
    register = json.loads(
        subprocess.check_output(
            ["git", "show", f"{HISTORY}:docs/audits/master-migration/decisions.json"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        )
    )
    assert not register["approved_schema_changes"] and register["schema_policy"] == "PRESERVE"
    routes, openapi = inventory()
    # Never overwrite the baseline with later stage state.
    for name, data in {
        "openapi_before.json": openapi,
        "api_inventory.json": routes,
    }.items():
        path = OUT / name
        if not path.exists():
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    header = "# API inventory - G0\n\nOffline registered route inspection + dynamic-template source scan. No startup/DB/.env.\n"
    header += "All routes KEEP; external consumers are UNKNOWN unless explicitly approved. Tests are usage evidence, not production clients.\n"
    header += "CLI/worker invoke shared application services, not HTTP; service_refs traces these without falsely marking their handlers dead.\n\n"
    header += "| Method | Path | Handler | Usage | Decision | Call sites |\n| --- | --- | --- | --- | --- | --- |\n"
    for row in routes:
        header += (
            f"| {row['method']} | {row['path']} | {row['handler']} | {row['classification']} | KEEP | "
            + ", ".join(row["call_sites"][:4])
            + " |\n"
        )
    if not (OUT / "api_inventory.md").exists():
        (OUT / "api_inventory.md").write_text(header, encoding="utf-8")
    security = "# Security matrix - baseline G0\n\nLocal admin config is NOT authentication. PUBLISHED is NOT public rights. No production identity provider.\n"
    security += "Middleware currently only limits POST /resources. Handler checks can occur after multipart parsing.\n\n"
    security += "| Method/path | Category | Current guard calls | Risk / intended boundary |\n| --- | --- | --- | --- |\n"
    for row in routes:
        security += (
            f"| {row['method']} {row['path']} | {row['category']} | {', '.join(row['guard_calls']) or 'NONE'} | "
            + (
                "Preserve public-safe DTO"
                if row["category"] == "READ_PUBLIC"
                else "Verified local-only; shared content access semantics required; no client identity grants"
            )
            + " |\n"
        )
    if not (OUT / "security_matrix.md").exists():
        (OUT / "security_matrix.md").write_text(security, encoding="utf-8")
    print(
        json.dumps(
            {
                "routes": len(routes),
                "documented": sum(r["documented"] for r in routes),
                "schema": schema["counts"],
                "historical_schema_parity": "PASS",
                "live": "NOT_RUN",
            }
        )
    )


if __name__ == "__main__":
    main()
