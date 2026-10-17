"""B04 tree acceptance, shared with isolated PostgreSQL tests."""
from datetime import timedelta

import pytest

from data_sync_etl.domain.core import now
from tests.unit.test_source_file_import import discover_and_import
from tests.unit.test_source_file_import import importing as importing


def add_version(rows, number, parent):
    row = {**rows[0], "source_version_id": f"source-version-{number}",
           "source_version_number": number, "parent_version_id": parent}
    rows.append(row)
    return row


def run(c):
    c.sync.run(full=True)
    return discover_and_import(c)


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
        assert repo.get("resource_versions", child["target_version_id"])["parent_version_id"] is None


def test_parent_pending_bounded_and_manual_recovery(importing, monkeypatch):
    c, rows, _ = importing
    root = rows.pop()
    rows.append({**root, "source_version_id": "source-version-3", "source_version_number": 3,
                 "parent_version_id": root["source_version_id"]})
    # Parent exists in SOURCE, but has not reached discovery yet.
    monkeypatch.setattr(c.source, "get_version", lambda id: next(
        (dict(r) for r in [root, *rows] if r["source_version_id"] == id), None), raising=False)
    c.settings.source_file_import_max_attempts = 2
    child = run(c)
    assert child["status"] == "RETRY_WAIT" and child["last_error_code"] == "PARENT_PENDING"
    assert child["target_version_id"] is None and c.source_file_import.claim() is None
    with c.uow() as repo:
        repo.update("source_file_imports", child["id"], {"next_retry_at": now() - timedelta(seconds=1)})
    c.source_file_import.execute(c.source_file_import.claim())
    with c.uow() as repo:
        exhausted = repo.get("source_file_imports", child["id"])
        assert exhausted["status"] == "RETRY_EXHAUSTED" and exhausted["next_retry_at"] is None
        assert exhausted["target_version_id"] is None
    rows.append(root)
    parent = run(c)
    assert parent["target_version_id"]
    c.source_file_import.retry(child["id"])
    c.source_file_import.execute(c.source_file_import.claim())
    with c.uow() as repo:
        child = repo.get("source_file_imports", child["id"])
        assert child["manual_retries"] == 1
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
    child = run(c)
    assert child["status"] == "FAILED" and child["last_error_code"] == code
    assert child["target_version_id"] is None


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
    claimed = c.source_file_import.claim()
    c.source_file_import.execute(claimed)
    with c.uow() as repo:
        child = repo.get("source_file_imports", claimed["id"])
        assert child["last_error_code"] == code and child["target_version_id"] is None
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
    assert row["status"] == "FAILED" and row["last_error_code"] == "BLOCKED_SOURCE_POLICY"
    assert row["target_version_id"] is None


def test_source_parent_changes_during_copy_rolls_back_mapping(importing, monkeypatch):
    c, rows, _ = importing
    root = run(c)
    add_version(rows, 2, rows[0]["source_version_id"])
    save = c.storage.save_stream
    def mutated(*args, **kwargs):
        result = save(*args, **kwargs)
        rows[1]["parent_version_id"] = None
        return result
    monkeypatch.setattr(c.storage, "save_stream", mutated)
    child = run(c)
    assert child["last_error_code"] == "SOURCE_VERSION_MUTATED" and child["target_version_id"] is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 1
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
    claim = c.source_file_import.claim()
    c.source_file_import.execute(claim)
    with c.uow() as repo:
        child = repo.get("source_file_imports", claim["id"])
        assert child["last_error_code"] == "PARENT_MAPPING_INVALID" and child["target_version_id"] is None
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
    assert pending["status"] == "RETRY_WAIT" and pending["attempts"] == 1
    rows.append(root)  # Parent becomes visible to the later discovery batch.
    parent = run(c)
    assert parent["source_version_id"] == "z-parent" and parent["target_version_id"]
    with c.uow() as repo:
        repo.update("source_file_imports", pending["id"], {"next_retry_at": now() - timedelta(seconds=1)})
    claimed = c.source_file_import.claim()
    assert claimed["id"] == pending["id"]
    c.source_file_import.execute(claimed)
    with c.uow() as repo:
        child = repo.get("source_file_imports", pending["id"])
        assert child["status"] == "PROCESSING" and child["manual_retries"] == 0 and child["attempts"] == 2
        assert repo.get("resource_versions", child["target_version_id"])["parent_version_id"] == parent["target_version_id"]
        assert repo.get("resource_versions", child["target_version_id"])["version_number"] == 3
