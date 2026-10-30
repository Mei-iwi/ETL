"""B04 tree acceptance, shared with isolated PostgreSQL tests."""
from datetime import timedelta

import pytest

from data_sync_etl.domain.core import SourceFileError, now
from tests.unit.test_source_file_import import importing as importing


def add_version(rows, number, parent):
    row = {**rows[0], "source_version_id": f"source-version-{number}",
           "source_version_number": number, "parent_version_id": parent}
    rows.append(row)
    return row


def run(c):
    c.sync.run(full=True)
    c.source_file_import.discover_once()
    with c.uow() as repo:
        return repo.find("source_file_imports", order=("-created_at", "-id"), limit=1)[0]


def test_source_branch_and_late_number(importing):
    c, rows, _ = importing
    root = run(c)
    for number in (3, 2):
        add_version(rows, number, rows[0]["source_version_id"])
        child = run(c)
        assert child["target_version_id"]
        with c.uow() as repo:
            version = repo.get("resource_versions", child["target_version_id"])
            assert version["parent_version_id"] == root["target_version_id"]
            assert version["version_number"] == number


def test_null_source_parent_stays_null(importing):
    c, rows, _ = importing
    run(c)
    add_version(rows, 2, None)
    child = run(c)
    with c.uow() as repo:
        child = repo.find("source_file_imports", {"source_version_id": rows[0]["source_version_id"]}, limit=1)[0]
    with c.uow() as repo:
        assert repo.get("resource_versions", child["target_version_id"])["parent_version_id"] is None


def test_parent_pending_bounded_and_manual_recovery(importing, monkeypatch):
    c, rows, _ = importing
    root = rows.pop()
    rows.append({**root, "source_version_id": "source-version-3", "source_version_number": 3,
                 "parent_version_id": root["source_version_id"]})
    # Parent exists in SOURCE, but has not reached discovery yet.
    monkeypatch.setattr(c.source, "get_version", lambda id: next(
        (dict(r) for r in [root, *rows] if r["source_version_id"] == id), None), raising=False)
    child = run(c)
    assert child["status"] == "METADATA_ONLY" and child["target_version_id"]
    assert c.source_file_import.claim() is None
    rows.append(root)
    parent = run(c)
    with c.uow() as repo:
        c.source_file_import._discover(repo, rows[-2])
    with c.uow() as repo:
        child = repo.get("source_file_imports", child["id"])
        assert child["status"] == "METADATA_ONLY" and child["manual_retries"] == 0
        assert repo.get("resource_versions", child["target_version_id"])["parent_version_id"] == parent["target_version_id"]


@pytest.mark.parametrize("fault,code", [("missing", "PARENT_SOURCE_MISSING"),
    ("self", "SOURCE_LINEAGE_CYCLE"), ("cycle", "SOURCE_LINEAGE_CYCLE"),
    ("cross", "PARENT_SOURCE_RESOURCE"), ("policy", "PARENT_SOURCE_POLICY")])
def test_invalid_parent_never_copies_bytes(importing, monkeypatch, fault, code):
    c, rows, _ = importing
    parent = add_version(rows, 2, None)
    rows[0]["parent_version_id"] = parent["source_version_id"]
    if fault == "missing":
        rows.pop()
    elif fault == "self":
        rows[0]["parent_version_id"] = rows[0]["source_version_id"]
    elif fault == "cycle":
        parent["parent_version_id"] = rows[0]["source_version_id"]
    elif fault == "cross":
        parent["resource_id"] = "z-other-resource"
    else:
        parent["source_lifecycle_status"] = "REJECTED"
    monkeypatch.setattr(c.source_files, "open_stream", lambda *_: pytest.fail("dependency must precede bytes"))
    run(c)
    with c.uow() as repo:
        child = repo.find("source_file_imports", {"source_version_id": rows[0]["source_version_id"]}, limit=1)[0]
    assert child["status"] == "METADATA_ONLY" and child["target_version_id"]
    with pytest.raises(SourceFileError, match="BLOCKED_SOURCE_(MAPPING|POLICY)"):
        c.source_resolver.resolve(child["target_version_id"])


@pytest.mark.parametrize("fault,code", [("origin", "PARENT_PENDING"),
    ("resource", "PARENT_MAPPING_INVALID"), ("failed", "PARENT_MAPPING_FAILED"),
    ("target", "PARENT_MAPPING_INVALID"), ("pending", "PARENT_PENDING")])
def test_mapping_must_be_committed_same_origin_resource(importing, fault, code):
    c, rows, _ = importing
    parent = run(c)
    add_version(rows, 2, rows[0]["source_version_id"])
    changes = {"origin": {"source_system": "other-source"},
        "resource": {"resource_id": "z-other"}, "failed": {"status": "FAILED"},
        "target": {"target_version_id": None}, "pending": {"status": "RETRY_WAIT", "next_retry_at": now() + timedelta(hours=1)}}
    with c.uow() as repo:
        repo.update("source_file_imports", parent["id"], changes[fault])
    # Discovery would legitimately discover a new origin mapping; isolate resolution here.
    with c.uow() as repo:
        c.source_file_import._discover(repo, rows[1])
        child = repo.find("source_file_imports", {"source_version_id": rows[1]["source_version_id"]})[0]
    with pytest.raises(SourceFileError, match="BLOCKED_SOURCE_MAPPING"):
        c.source_resolver.resolve(child["target_version_id"])
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.get("resource_versions", parent["target_version_id"])["parent_version_id"] is None


def test_successful_parent_change_is_reported_without_repair(importing):
    c, rows, _ = importing
    root = run(c)
    add_version(rows, 2, rows[0]["source_version_id"])
    child = run(c)
    rows[1]["parent_version_id"] = None
    c.sync.run(full=True)
    c.source_file_import.discover_once()
    with c.uow() as repo:
        reported = repo.get("source_file_imports", child["id"])
        assert reported["last_error_code"] == "SOURCE_VERSION_MUTATED"
        assert reported["target_version_id"] == child["target_version_id"]
        assert repo.get("resource_versions", child["target_version_id"])["parent_version_id"] == root["target_version_id"]


@pytest.mark.parametrize("fault", ["disabled", "deleted"])
def test_inactive_source_resource_blocks_before_bytes(importing, monkeypatch, fault):
    c, rows, _ = importing
    rows[0]["resource_active"] = fault != "disabled"
    rows[0]["resource_deleted_at"] = now() if fault == "deleted" else None
    monkeypatch.setattr(c.source_files, "open_stream", lambda *_: pytest.fail("inactive source copied"))
    row = run(c)
    assert row["status"] == "METADATA_ONLY" and row["target_version_id"]
    with pytest.raises(SourceFileError, match="BLOCKED_SOURCE_POLICY"):
        c.source_resolver.resolve(row["target_version_id"])


def test_source_parent_changes_during_verification_rolls_back_mapping(importing, monkeypatch):
    c, rows, _ = importing
    root = run(c)
    child_source = add_version(rows, 2, rows[0]["source_version_id"])
    child = run(c)
    child_source["parent_version_id"] = None
    c.sync.run(full=True)
    c.source_file_import.discover_once()
    with c.uow() as repo:
        current = repo.get("source_file_imports", child["id"])
        assert current["last_error_code"] == "SOURCE_VERSION_MUTATED" and current["target_version_id"]
        assert repo.count("resource_versions", {}) == 2
        assert repo.get("resource_versions", root["target_version_id"])["parent_version_id"] is None



def test_historical_wrong_tree_blocks_descendant_without_repair(importing):
    c, rows, _ = importing
    root = run(c)
    add_version(rows, 2, rows[0]["source_version_id"])
    parent = run(c)
    # Isolated corruption fixture only: runtime must not repair this history.
    with c.uow() as repo:
        repo.update("resource_versions", parent["target_version_id"], {"parent_version_id": None})
    add_version(rows, 3, rows[1]["source_version_id"])
    with c.uow() as repo:
        c.source_file_import._discover(repo, rows[2])
        child = repo.find("source_file_imports", {"source_version_id": rows[2]["source_version_id"]})[0]
    with pytest.raises(SourceFileError, match="BLOCKED_SOURCE_MAPPING"):
        c.source_resolver.resolve(child["target_version_id"])
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.get("resource_versions", parent["target_version_id"])["parent_version_id"] is None
        assert repo.get("resource_versions", root["target_version_id"])["parent_version_id"] is None



def test_parent_arrival_automatically_recovers_before_retry_budget(importing, monkeypatch):
    c, rows, _ = importing
    root = {**rows.pop(), "source_version_id": "z-parent"}
    rows.append({**root, "source_version_id": "a-child", "source_version_number": 3,
                 "parent_version_id": "z-parent"})
    monkeypatch.setattr(c.source, "get_version", lambda id: next(
        (dict(r) for r in [root, *rows] if r["source_version_id"] == id), None), raising=False)
    pending = run(c)
    assert pending["status"] == "METADATA_ONLY" and pending["attempts"] == 0
    rows.append(root)  # Parent becomes visible to the later discovery batch.
    parent = run(c)
    assert parent["source_version_id"] == "z-parent" and parent["target_version_id"]
    with c.uow() as repo:
        c.source_file_import._discover(repo, rows[0])
    with c.uow() as repo:
        child = repo.get("source_file_imports", pending["id"])
        assert child["status"] == "METADATA_ONLY" and child["manual_retries"] == 0 and child["attempts"] == 0
        assert repo.get("resource_versions", child["target_version_id"])["parent_version_id"] == parent["target_version_id"]
        assert repo.get("resource_versions", child["target_version_id"])["version_number"] == 3


@pytest.mark.parametrize("base_seconds", [3000, 3600])
def test_parent_retry_jitter_respects_final_one_hour_cap(importing, monkeypatch, base_seconds):
    c, rows, _ = importing
    root = rows.pop()
    rows.append({**root, "source_version_id": "source-version-3", "source_version_number": 3,
                 "parent_version_id": root["source_version_id"]})
    monkeypatch.setattr(c.source, "get_version", lambda id: next(
        (dict(r) for r in [root, *rows] if r["source_version_id"] == id), None), raising=False)
    c.settings.source_file_import_retry_seconds = base_seconds
    stamp = now()
    monkeypatch.setattr("data_sync_etl.application.source_file_import.now", lambda: stamp)
    monkeypatch.setattr("data_sync_etl.application.source_file_import.random.uniform", lambda low, high: high)
    pending = run(c)
    assert pending["status"] == "METADATA_ONLY" and pending["next_retry_at"] is None
    assert c.source_file_import.claim() is None
