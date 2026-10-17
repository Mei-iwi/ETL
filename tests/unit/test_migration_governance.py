import ast
import copy
import json
from pathlib import Path

import pytest

from scripts.check_migration_governance import (
    BASELINE,
    ROOT,
    canonical_functions,
    check,
    compare_fields,
    digest,
    expected_mongo_manifest,
    expected_references,
    read,
    select_only,
)


def test_read_only_governance_is_repeatable_and_preserves_contract():
    first, second = check(), check()
    assert first == second
    assert first["status"] == "PASS" and first["live_applied"] is False
    assert (first["source_tables"], first["master_tables"], first["mongo_collections"]) == (
        15,
        18,
        5,
    )
    assert first["embedding_dimension"] == 384 and first["business_indexes"] == 6
    assert first["heads"] == {"SOURCE": ["s0003"], "MASTER": ["0015"]}
    assert set(first["blocked_design_decisions"]) == {"B03", "B04", "B07", "B08", "B09"}


def test_manifest_fingerprints_are_order_independent():
    assert digest({"b": 2, "a": 1}) == digest({"a": 1, "b": 2})


@pytest.mark.parametrize(
    "change",
    [
        {"dimension": 256},
        {
            "collections": [
                "users",
                "learning_resources",
                "ocr_pages",
                "mongo_content_units",
                "indexed_chunks",
            ]
        },
        {"validators_sha256": "0" * 64},
        {"business_index_count": 7},
        {"mode": "APPLY"},
    ],
)
def test_contract_manifest_drift_is_detected(change):
    baseline = json.loads(read(BASELINE))
    expected = expected_mongo_manifest(baseline["mongo"])
    altered = copy.deepcopy(expected)
    altered.update(change)
    assert compare_fields(expected, altered, "manifest")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; UPDATE x SET id=1;",
        "CREATE TABLE x(id int);",
        "SELECT 1; DELETE FROM x;",
        "-- comment only",
    ],
)
def test_verification_sql_rejects_non_select_statements(sql):
    assert not select_only(sql)


def test_select_only_catalog_is_explicit_and_has_no_public_schema_assumption():
    sql = read("scripts/sql/verify/catalog.sql")
    assert select_only(sql)
    assert "current_schema()" in sql and "WHERE n.nspname = current_schema()" in sql
    assert "public." not in sql
    assert select_only(read("scripts/sql/verify/master_preflight.sql"))


def test_canonical_references_match_frozen_functions_and_triggers():
    references = expected_references()
    assert all(read(path) == body for path, body in references.items())
    assert len(canonical_functions()) == 6
    source = references["alembic_source/sql/triggers/source_current.sql"]
    master = references["alembic/sql/triggers/master_current.sql"]
    assert source.count("CREATE TRIGGER ") == 26
    assert master.count("CREATE TRIGGER ") == 6
    assert "source_touch_dependents('users', 'role_id')" in source
    assert "trg_content_parent_guard BEFORE INSERT OR UPDATE OF" in master


def test_reference_sql_is_not_silently_loaded_by_historical_migrations():
    for folder in ("alembic/versions", "alembic_source/versions"):
        for path in (ROOT / folder).glob("*.py"):
            assert "/sql/triggers/" not in path.read_text(encoding="utf-8")


def test_docker_copies_reference_directories_without_runtime_install_hooks():
    dockerfile = read("Dockerfile")
    assert "COPY alembic ./alembic" in dockerfile
    assert "COPY alembic_source ./alembic_source" in dockerfile
    # References are checkout/Docker governance artifacts, not Python runtime resources.
    assert "check_migration_governance" not in read("src/data_sync_etl/main.py")


def test_checker_has_no_apply_mode_or_database_client():
    path = Path(__file__).parents[2] / "scripts/check_migration_governance.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not {"pymongo", "psycopg", "sqlalchemy"} & imports
    assert 'parser.add_argument("--apply"' not in path.read_text(encoding="utf-8")
