"""Read-only migration governance checks; no dotenv, DB connections, or apply mode."""

import argparse
import ast
import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.audit_erd_contract import collect, readonly_guard, trigger_inventory  # noqa: E402

BASELINE = "docs/audits/erd-contract/contract_snapshot.json"
MONGO_MANIFEST = "migrations_mongo/manifest.json"
REGISTER = "docs/audits/master-migration/decisions.json"
REFERENCE_PATHS = {
    "MASTER": "alembic/sql/triggers/master_current.sql",
    "SOURCE": "alembic_source/sql/triggers/source_current.sql",
}
VERIFY_PATHS = ("scripts/sql/verify/catalog.sql", "scripts/sql/verify/master_preflight.sql")


def read(path, root=ROOT):
    return (root / path).read_text(encoding="utf-8-sig")


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def literal_upgrade_sql(path):
    """Read string literals from a historical revision without importing/executing it."""
    tree = ast.parse(read(path))
    upgrade = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "upgrade"
    )
    return "\n".join(
        textwrap.dedent(node.args[0].value)
        for node in ast.walk(upgrade)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
        and node.func.attr == "execute"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    )


def extract_function(sql, name):
    pattern = r"CREATE(?: OR REPLACE)? FUNCTION " + re.escape(name) + r"\(\).*?\$\$;"
    found = re.search(pattern, sql, re.S)
    if found is None:
        raise ValueError("Missing canonical function: " + name)
    return found[0].strip() + "\n"


def canonical_functions():
    sources = {
        "source_set_updated_at": ("SOURCE", "alembic_source/timestamps_v2.sql"),
        "source_require_soft_delete": ("SOURCE", "alembic_source/triggers_v1.sql"),
        "source_touch_resource": ("SOURCE", "alembic_source/triggers_v1.sql"),
        "source_touch_dependents": ("SOURCE", "alembic_source/triggers_v1.sql"),
        "master_set_local_updated_at": ("MASTER", "alembic/versions/0007_timestamp_integrity.py"),
        "master_guard_content_parent": ("MASTER", "alembic/versions/0006_source_compatibility.py"),
    }
    result = {}
    for name, (layer, path) in sources.items():
        sql = literal_upgrade_sql(path) if path.endswith(".py") else read(path)
        result[name] = {"layer": layer, "source": path, "sql": extract_function(sql, name)}
    return result


def expected_references():
    functions = canonical_functions()
    triggers = trigger_inventory()
    dependents = {
        "providers": ("learning_resources", "provider_id"),
        "resource_types": ("learning_resources", "resource_type_id"),
        "roles": ("users", "role_id"),
        "education_levels": ("grade_levels", "education_level_id"),
    }
    result = {}
    for layer, path in REFERENCE_PATHS.items():
        parts = [
            "-- REFERENCE ONLY. Never executed by existing migrations or API startup.",
            "-- Derived from frozen revisions; do not use this file as an installer.",
            "-- Historical files remain the replay authority. Comments are not copied.",
        ]
        for spec in functions.values():
            if spec["layer"] == layer:
                parts.extend(["", "-- Origin: " + spec["source"], spec["sql"].rstrip()])
        parts.append("")
        for trigger in triggers:
            if trigger["layer"] != layer:
                continue
            arguments = ""
            if trigger["function"] == "source_touch_dependents":
                arguments = ", ".join("'" + value + "'" for value in dependents[trigger["table"]])
            event = trigger["event"].replace("/", " OR ")
            parts.append(
                "CREATE TRIGGER "
                + trigger["trigger"]
                + " "
                + event
                + " ON "
                + trigger["table"]
                + " FOR EACH ROW EXECUTE FUNCTION "
                + trigger["function"]
                + "("
                + arguments
                + ");"
            )
        result[path] = "\n".join(parts) + "\n"
    return result


def expected_mongo_manifest(mongo):
    return {
        "manifest_version": 1,
        "mode": "CHECK_ONLY",
        "schema_version": mongo["schema_version"],
        "dimension": mongo["dimension"],
        "collections": sorted(mongo["validators"]),
        "business_index_count": sum(len(values) for values in mongo["indexes"].values()),
        "validators_sha256": digest(mongo["validators"]),
        "indexes_sha256": digest(mongo["indexes"]),
        "installer": "python -m data_sync_etl.adapters.mongo.schema",
        "read_only_flags": ["--inventory", "--check"],
        "bootstrap_flag": "--bootstrap-empty",
        "apply_flag": "--apply",
        "apply_policy": "Existing CLI only; separate target, backup, compatibility checks and operator approval required.",
        "external_state": "This Git manifest is not a live migration receipt; no schema_migrations collection.",
        "historical_issue": "B02 Fallback documentation unresolved; preserve current runtime contract.",
    }


def compare_fields(expected, actual, prefix):
    return [
        prefix + "." + key
        for key in sorted(expected.keys() | actual.keys())
        if key not in expected or key not in actual or expected[key] != actual[key]
    ]


def select_only(sql):
    """A bounded reference-file check, not a security parser for arbitrary SQL."""
    sql = re.sub(r"--[^\n]*", "", sql)
    statements = [value.strip() for value in sql.split(";") if value.strip()]
    return bool(statements) and all(re.match(r"SELECT\b", value, re.I) for value in statements)


def check(root=ROOT):
    baseline = json.loads(read(BASELINE, root))
    _, current = collect()
    # HEAD differs after merge. Contract hashes/data, not branch topology, are authoritative.
    immutable = (
        "references",
        "implementation_sha256",
        "source",
        "source_enums",
        "master",
        "mongo",
        "chains",
    )
    encoded = json.loads(json.dumps(current))
    drift = ["baseline." + key for key in immutable if encoded[key] != baseline[key]]
    for path, expected in expected_references().items():
        if not (root / path).is_file() or read(path, root) != expected:
            drift.append("canonical." + path)
    manifest = json.loads(read(MONGO_MANIFEST, root))
    drift += compare_fields(expected_mongo_manifest(encoded["mongo"]), manifest, "mongo_manifest")
    for path in VERIFY_PATHS:
        if not select_only(read(path, root)):
            drift.append("non_read_only." + path)
    register = json.loads(read(REGISTER, root))
    if register["approved_schema_changes"]:
        drift.append("scope.unexpected_schema_approval")
    if register["schema_policy"] != "PRESERVE":
        drift.append("scope.schema_policy")
    pending = [row["id"] for row in register["design_decisions"] if row["status"] != "APPROVED"]
    return {
        "status": "FAIL" if drift else "PASS",
        "scope": "STATIC_GOVERNANCE_ONLY",
        "drift": drift,
        "source_tables": len(encoded["source"]),
        "master_tables": len(encoded["master"]),
        "mongo_collections": len(encoded["mongo"]["validators"]),
        "embedding_dimension": manifest["dimension"],
        "business_indexes": manifest["business_index_count"],
        "heads": {layer: value["heads"] for layer, value in encoded["chains"].items()},
        "blocked_design_decisions": pending,
        "known_historical_defects": [
            "B13: 0005 downgrade index name mismatch; frozen revision unchanged"
        ],
        "live_applied": False,
    }


def main():
    sys.addaudithook(readonly_guard)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()  # Deliberately no apply/write/target-url options.
    try:
        result = check()
    except (OSError, ValueError, KeyError, RuntimeError):
        print(
            json.dumps(
                {
                    "status": "FAIL",
                    "reason": "Governance inputs missing or invalid; inspect repository files.",
                    "live_applied": False,
                }
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return int(result["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
