import json
from datetime import UTC, datetime

import pytest

from data_sync_etl.domain.core import DomainError


def change(container, file, transform):
    path = container.source.root / (file + ".json")
    data = json.loads(path.read_text())
    transform(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_insert_skip_and_repository_secret(container):
    first = container.sync.run(full=True)
    assert first["resources"]["inserted"] == 3
    second = container.sync.run(full=True)
    assert all(run["inserted"] == run["updated"] == 0 for run in second.values())
    assert second["resources"]["skipped"] == 3
    with container.uow() as repo:
        assert "hashed_password" not in repo.get("master_users", "user-1")
        assert repo.count("resource_subjects", {}) == 3


@pytest.mark.parametrize(
    "file,index,field,value,stream,table,target,mapped",
    [
        (
            "providers",
            1,
            "name",
            "Renamed",
            "resources",
            "master_learning_resources",
            "resource-1",
            "provider_name",
        ),
        ("roles", 0, "name", "New role", "users", "master_users", "user-1", "role_name"),
        (
            "education_levels",
            0,
            "name_vi",
            "New education",
            "grades",
            "master_grade_levels",
            "grade-1",
            "education_name_vi",
        ),
    ],
)
def test_parent_propagation_incremental(
    container, file, index, field, value, stream, table, target, mapped
):
    container.sync.run(full=True)
    change(
        container,
        file,
        lambda rows: rows[index].update({field: value, "updated_at": "2026-02-01T00:00:00Z"}),
    )
    result = container.sync.run(stream, full=False)
    assert result[stream]["updated"] >= 1
    with container.uow() as repo:
        assert repo.get(table, target)[mapped] == value


@pytest.mark.parametrize(
    "stream,table",
    [
        ("resources", "master_learning_resources"),
        ("users", "master_users"),
        ("subjects", "subjects"),
    ],
)
def test_soft_delete(container, stream, table):
    container.sync.run(full=True)
    change(
        container,
        stream,
        lambda rows: rows[0].update(
            is_active=False, deleted_at="2026-02-01T00:00:00Z", updated_at="2026-02-01T00:00:00Z"
        ),
    )
    container.sync.run(stream)
    with container.uow() as repo:
        target = repo.get(
            table, {"resources": "resource-1", "users": "user-1", "subjects": "subject-1"}[stream]
        )
        assert target["is_active"] is False and target["deleted_at"] is not None


def test_relations_replace(container):
    container.sync.run(full=True)
    change(
        container,
        "resource_subjects",
        lambda rows: rows[0].update(
            subject_id="subject-2", is_primary=False, updated_at="2026-02-01T00:00:00Z"
        ),
    )
    container.sync.run("resources")
    with container.uow() as repo:
        assert repo.find("resource_subjects", {"resource_id": "resource-1"}) == [
            dict(resource_id="resource-1", subject_id="subject-2", is_primary=False)
        ]
    # Removal must bump the owning resource's updated_at in the source contract.
    change(container, "resource_subjects", lambda rows: rows.pop(0))
    change(container, "resources", lambda rows: rows[0].update(updated_at="2026-03-01T00:00:00Z"))
    container.sync.run("resources")
    with container.uow() as repo:
        assert repo.count("resource_subjects", {"resource_id": "resource-1"}) == 0


def test_failed_batch_rolls_back_and_restart(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        before = repo.get("sync_checkpoints", "resources")
    change(
        container,
        "resources",
        lambda rows: rows[0].update(title="Changed", updated_at="2026-02-01T00:00:00Z"),
    )
    change(container, "resource_subjects", lambda rows: rows[0].update(subject_id="missing"))
    with pytest.raises(DomainError):
        container.sync.run("resources")
    with container.uow() as repo:
        assert repo.get("sync_checkpoints", "resources") == before
        assert repo.get("master_learning_resources", "resource-1")["title"] != "Changed"
        assert (
            repo.find("sync_runs", {"stream_name": "resources"}, order=("-started_at",), limit=1)[
                0
            ]["status"]
            == "FAILED"
        )
    change(container, "resource_subjects", lambda rows: rows[0].update(subject_id="subject-1"))
    assert container.sync.run("resources")["resources"]["updated"] == 1


def test_incremental_same_timestamp_restart(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update(
            "sync_checkpoints",
            "resources",
            dict(last_updated_at=datetime(2026, 1, 1, tzinfo=UTC), last_id="resource-1"),
        )
    result = container.sync.run("resources")
    assert result["resources"]["records_read"] == 2
    assert result["resources"]["skipped"] == 2


def test_logs_do_not_expose_secret(container, caplog):
    with caplog.at_level("INFO"):
        container.sync.run(full=True)
    assert "DEMO_NON_AUTHENTICATING_HASH" not in caplog.text
    assert "hashed_password" not in caplog.text
    assert "correlation_id" in caplog.text


def test_restart_keeps_prior_committed_batch(container):
    container.sync.run("grades", full=True)
    container.sync.run("subjects", full=True)
    change(container, "resource_subjects", lambda rows: rows[2].update(subject_id="missing"))
    with pytest.raises(DomainError):
        container.sync.run("resources", full=True)
    with container.uow() as repo:
        assert repo.count("master_learning_resources", {}) == 2
        assert repo.get("sync_checkpoints", "resources")["last_id"] == "resource-2"
    change(container, "resource_subjects", lambda rows: rows[2].update(subject_id="subject-3"))
    result = container.sync.run("resources")
    assert result["resources"]["inserted"] == 1
    with container.uow() as repo:
        assert repo.count("master_learning_resources", {}) == 3
