from datetime import timedelta

import pytest

from data_sync_etl.application.auto_sync import AutoSyncCoordinator
from data_sync_etl.domain.core import AutoSyncBaselineRequired, AutoSyncUnavailable, now
from data_sync_etl.workers.master_sync import MasterSyncWorker


def coordinator(container, *, supported=True):
    return AutoSyncCoordinator(
        container.uow,
        environment_supported=supported,
        source_health=lambda: None,
        interval_seconds=30,
        worker_stale_seconds=15,
        lease_seconds=30,
    )


def make_due(container):
    with container.uow() as repo:
        repo.update(
            "sync_auto_config",
            "master-data",
            {"next_run_at": now() - timedelta(seconds=1)},
        )


def test_auto_off_by_default_and_worker_health_is_real(container):
    auto = coordinator(container)
    assert auto.state()["auto_enabled"] is False
    assert auto.state()["auto_supported"] is False

    assert auto.heartbeat_and_claim("worker-1") == []
    state = auto.state()
    assert state["worker_healthy"] is True
    assert state["auto_supported"] is True
    assert state["auto_running"] is False


def test_auto_requires_supported_postgres_environment_and_full_baseline(container):
    unavailable = coordinator(container, supported=False)
    unavailable.heartbeat_and_claim("worker-1")
    with pytest.raises(AutoSyncUnavailable):
        unavailable.configure(True)

    auto = coordinator(container)
    auto.heartbeat_and_claim("worker-1")
    with pytest.raises(AutoSyncBaselineRequired):
        auto.configure(True)


def test_source_failure_keeps_auto_unsupported(container):
    def unavailable_source():
        raise RuntimeError("source offline")

    auto = AutoSyncCoordinator(
        container.uow,
        environment_supported=True,
        source_health=unavailable_source,
        interval_seconds=30,
        worker_stale_seconds=15,
        lease_seconds=30,
    )
    assert auto.heartbeat_and_claim("worker-1") == []
    state = auto.state()
    assert state["worker_healthy"] is True
    assert state["auto_supported"] is False
    assert state["auto_error"] == "SOURCE_UNAVAILABLE"
    with pytest.raises(AutoSyncUnavailable):
        auto.configure(True)


def test_auto_config_persists_and_worker_runs_incremental_only(container):
    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")
    auto.configure(True)

    restarted = coordinator(container)
    assert restarted.state()["auto_enabled"] is True
    assert MasterSyncWorker(container.sync, restarted, poll_seconds=1).once("worker-1") == {
        "action": "IDLE"
    }
    make_due(container)
    result = MasterSyncWorker(container.sync, restarted, poll_seconds=1).once("worker-1")

    assert result == {"action": "COMPLETED"}
    state = restarted.state()
    assert state["auto_enabled"] is True
    assert state["auto_running"] is False
    assert state["last_auto_run_at"] is not None
    with container.uow() as repo:
        latest = repo.find("sync_runs", order=("-started_at",), limit=4)
    assert {run["sync_mode"] for run in latest} == {"INCREMENTAL"}


def test_auto_lease_prevents_duplicate_worker_claim(container):
    first = coordinator(container)
    second = coordinator(container)
    container.sync.run(full=True)
    first.heartbeat_and_claim("worker-1")
    first.configure(True)
    make_due(container)

    assert set(first.heartbeat_and_claim("worker-1")) == {
        "resources", "users", "grades", "subjects"
    }
    assert second.heartbeat_and_claim("worker-2") == []
    assert first.complete("worker-1", None) is True


def test_auto_skips_when_manual_sync_lock_is_busy(container):
    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")
    auto.configure(True)
    make_due(container)
    worker = MasterSyncWorker(container.sync, auto, poll_seconds=1)

    with container.uow.sync_lock() as acquired:
        assert acquired is True
        assert worker.once("worker-1") == {"action": "SKIPPED"}

    state = auto.state()
    assert state["auto_error"] == "SYNC_BUSY"
    assert state["auto_running"] is False


def test_disabling_auto_does_not_require_a_healthy_worker(container):
    auto = coordinator(container, supported=False)
    auto.configure(False)
    assert auto.state()["auto_enabled"] is False


def test_disabling_auto_does_not_cancel_an_active_lease(container):
    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")
    auto.configure(True)
    make_due(container)
    assert set(auto.heartbeat_and_claim("worker-1")) == {
        "resources", "users", "grades", "subjects"
    }

    auto.configure(False)
    state = auto.state()
    assert state["auto_enabled"] is False
    assert state["auto_running"] is True
    assert auto.complete("worker-1", None) is True
    assert auto.state()["next_run_at"] is None


def test_failed_auto_run_waits_until_the_next_interval(container):
    class FailingSync:
        def run(self, stream, full):
            raise RuntimeError("failure must not escape the worker")

    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")
    auto.configure(True)
    make_due(container)
    result = MasterSyncWorker(FailingSync(), auto, poll_seconds=1).once("worker-1")

    assert result == {"action": "FAILED"}
    state = auto.state()
    assert state["auto_error"] == "SYNC_FAILED"
    assert state["next_run_at"] > state["last_auto_run_at"]


def test_stale_worker_is_not_reported_as_supported(container):
    auto = coordinator(container)
    auto.heartbeat_and_claim("worker-1")
    with container.uow() as repo:
        repo.update(
            "sync_auto_config",
            "master-data",
            {"heartbeat_at": now() - timedelta(seconds=16)},
        )
    state = auto.state()
    assert state["worker_healthy"] is False
    assert state["auto_supported"] is False


def test_once_worker_marks_itself_offline_when_process_exits(container):
    auto = coordinator(container)
    worker = MasterSyncWorker(container.sync, auto, poll_seconds=1)
    assert worker.run("worker-1", once=True) == {"action": "IDLE"}
    assert auto.state()["worker_healthy"] is False


def test_streams_can_be_enabled_individually_or_together(container):
    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")

    auto.configure(True, "resources")
    state = auto.state()
    assert state["auto_enabled"] is True
    assert state["auto_all_enabled"] is False
    assert [item["stream_name"] for item in state["auto_streams"] if item["enabled"]] == [
        "resources"
    ]
    make_due(container)
    assert MasterSyncWorker(container.sync, auto).once("worker-1") == {"action": "COMPLETED"}
    with container.uow() as repo:
        latest = repo.find("sync_runs", order=("-started_at",), limit=1)[0]
    assert latest["stream_name"] == "resources"
    assert latest["sync_mode"] == "INCREMENTAL"

    auto.configure(True, "all")
    assert auto.state()["auto_all_enabled"] is True
    auto.configure(False, "subjects")
    state = auto.state()
    assert state["auto_all_enabled"] is False
    assert state["auto_enabled"] is True
    auto.configure(False, "all")
    assert auto.state()["auto_enabled"] is False


def test_modes_persist_independently_and_worker_obeys_them(container):
    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")
    auto.configure(True)
    auto.configure(None, "resources", "FULL")
    restarted = coordinator(container)
    state = restarted.state()
    assert state["auto_all_enabled"]
    assert next(row for row in state["auto_streams"] if row["stream_name"] == "resources")["sync_mode"] == "FULL"
    make_due(container)
    assert MasterSyncWorker(container.sync, restarted).once("worker-1") == {"action": "COMPLETED"}
    with container.uow() as repo:
        runs = repo.find("sync_runs", order=("-started_at",), limit=4)
    assert {row["stream_name"]: row["sync_mode"] for row in runs} == {
        "resources": "FULL", "users": "INCREMENTAL", "grades": "INCREMENTAL", "subjects": "INCREMENTAL",
    }


def test_bulk_mode_change_preserves_partial_enabled_selection(container):
    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")
    auto.configure(True, "resources")
    auto.configure(None, "all", "FULL")
    state = auto.state()
    assert [row["stream_name"] for row in state["auto_streams"] if row["enabled"]] == ["resources"]
    assert {row["sync_mode"] for row in state["auto_streams"]} == {"FULL"}


def test_mode_change_rejected_while_auto_is_running(container):
    auto = coordinator(container)
    container.sync.run(full=True)
    auto.heartbeat_and_claim("worker-1")
    auto.configure(True)
    make_due(container)
    assert auto.heartbeat_and_claim("worker-1")
    with pytest.raises(AutoSyncUnavailable):
        auto.configure(None, "resources", "FULL")
    auto.configure(False)  # Stopping future runs still works during an active run.
