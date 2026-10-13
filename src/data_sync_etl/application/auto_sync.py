from datetime import timedelta

from data_sync_etl.domain.core import (
    AutoSyncBaselineRequired,
    AutoSyncUnavailable,
    now,
)
from data_sync_etl.ports.repositories import UnitOfWork

CONFIG_ID = "master-data"
AUTO_STREAMS = ("resources", "users", "grades", "subjects")


class AutoSyncCoordinator:
    def __init__(
        self,
        uow: UnitOfWork,
        *,
        environment_supported: bool,
        source_health=None,
        interval_seconds: int,
        worker_stale_seconds: int,
        lease_seconds: int,
    ):
        self.uow = uow
        self.environment_supported = environment_supported
        self.source_health = source_health
        self.interval_seconds = interval_seconds
        self.worker_stale_seconds = worker_stale_seconds
        self.lease_seconds = lease_seconds

    def _ensure(self, repo):
        repo.ensure_auto_sync_config(self.interval_seconds)
        return repo.get("sync_auto_config", CONFIG_ID, lock=True)

    @staticmethod
    def _baseline_ready(repo, streams):
        return all(
            repo.find(
                "sync_runs",
                {"stream_name": stream, "sync_mode": "FULL", "status": "COMPLETED"},
                limit=1,
            )
            for stream in streams
        )

    @staticmethod
    def _stream_state(repo):
        rows = repo.find("sync_auto_streams")
        enabled = {row["stream_name"]: row["enabled"] for row in rows}
        modes = {row["stream_name"]: row["sync_mode"] for row in rows}
        return [
            {
                "stream_name": stream,
                "enabled": enabled.get(stream, False),
                "sync_mode": modes.get(stream, "INCREMENTAL"),
            }
            for stream in AUTO_STREAMS
        ]

    def state(self):
        current = now()
        with self.uow() as repo:
            config = repo.get("sync_auto_config", CONFIG_ID)
            streams = self._stream_state(repo) if config is not None else []
        if config is None:
            return {
                "auto_supported": False,
                "auto_enabled": False,
                "interval_seconds": self.interval_seconds,
                "worker_healthy": False,
                "auto_running": False,
                "last_auto_run_at": None,
                "next_run_at": None,
                "auto_error": None,
                "auto_all_enabled": False,
                "auto_streams": [
                    {"stream_name": stream, "enabled": False, "sync_mode": "INCREMENTAL"}
                    for stream in AUTO_STREAMS
                ],
            }
        worker_healthy = bool(
            config["heartbeat_at"]
            and config["heartbeat_at"] >= current - timedelta(seconds=self.worker_stale_seconds)
        )
        auto_running = bool(
            config["lease_owner"]
            and config["lease_expires_at"]
            and config["lease_expires_at"] > current
        )
        return {
            "auto_supported": (
                self.environment_supported and worker_healthy and config["source_ready"]
            ),
            "auto_enabled": config["enabled"],
            "interval_seconds": config["interval_seconds"],
            "worker_healthy": worker_healthy,
            "auto_running": auto_running,
            "last_auto_run_at": config["last_auto_run_at"],
            "next_run_at": config["next_run_at"],
            "auto_error": config["last_error_code"],
            "auto_all_enabled": all(stream["enabled"] for stream in streams),
            "auto_streams": streams,
        }

    def configure(self, enabled: bool | None, stream="all", sync_mode=None):
        if stream != "all" and stream not in AUTO_STREAMS:
            raise ValueError("Unknown Auto Sync stream")
        if sync_mode not in {None, "INCREMENTAL", "FULL"}:
            raise ValueError("Unknown Auto Sync mode")
        current = now()
        with self.uow() as repo:
            config = self._ensure(repo)
            target_streams = list(AUTO_STREAMS) if stream == "all" else [stream]
            if sync_mode is not None and config["lease_expires_at"] and config["lease_expires_at"] > current:
                raise AutoSyncUnavailable("Wait for the active sync before changing mode")
            if enabled:
                if not self.environment_supported:
                    raise AutoSyncUnavailable("Auto sync requires PostgreSQL source and Master DB")
                if not config["source_ready"]:
                    raise AutoSyncUnavailable("Auto sync source is unavailable")
                heartbeat = config["heartbeat_at"]
                if not heartbeat or heartbeat < current - timedelta(
                    seconds=self.worker_stale_seconds
                ):
                    raise AutoSyncUnavailable("Auto sync worker is unavailable")
                if not self._baseline_ready(repo, target_streams):
                    raise AutoSyncBaselineRequired("A completed Full baseline is required")
            stream_rows = self._stream_state(repo)
            stream_enabled = {row["stream_name"]: row["enabled"] for row in stream_rows}
            for target in target_streams:
                if enabled is not None:
                    stream_enabled[target] = enabled
                repo.update(
                    "sync_auto_streams",
                    target,
                    {"enabled": stream_enabled[target], "updated_at": current,
                     **({"sync_mode": sync_mode} if sync_mode is not None else {})},
                )
            any_enabled = any(stream_enabled.values())
            repo.update(
                "sync_auto_config",
                CONFIG_ID,
                {
                    "enabled": any_enabled,
                    "next_run_at": (
                        current + timedelta(seconds=config["interval_seconds"])
                        if any_enabled and not config["enabled"]
                        else config["next_run_at"] if any_enabled else None
                    ),
                    "last_error_code": None,
                    "updated_at": current,
                },
            )

    def heartbeat_and_claim(self, worker_id: str):
        current = now()
        source_ready = False
        if self.environment_supported and self.source_health is not None:
            try:
                self.source_health()
                source_ready = True
            except Exception:
                source_ready = False
        with self.uow() as repo:
            config = self._ensure(repo)
            enabled_streams = [
                row["stream_name"]
                for row in self._stream_state(repo)
                if row["enabled"]
            ]
            updates = {
                "worker_id": worker_id,
                "heartbeat_at": current,
                "source_ready": source_ready,
                "updated_at": current,
            }
            if not self.environment_supported:
                updates["last_error_code"] = "UNSUPPORTED_ENVIRONMENT"
            elif not source_ready:
                updates["last_error_code"] = "SOURCE_UNAVAILABLE"
            elif config["last_error_code"] in {
                "SOURCE_UNAVAILABLE",
                "UNSUPPORTED_ENVIRONMENT",
            }:
                updates["last_error_code"] = None
            due = config["next_run_at"] is None or config["next_run_at"] <= current
            lease_free = (
                config["lease_expires_at"] is None or config["lease_expires_at"] <= current
            )
            claimed = bool(source_ready and enabled_streams and due and lease_free)
            if claimed:
                updates.update(
                    lease_owner=worker_id,
                    lease_expires_at=current + timedelta(seconds=self.lease_seconds),
                )
            repo.update("sync_auto_config", CONFIG_ID, updates)
            return enabled_streams if claimed else []

    def mode(self, stream):
        with self.uow() as repo:
            row = repo.get("sync_auto_streams", stream)
        return row["sync_mode"] if row else "INCREMENTAL"

    def mark_worker_stopped(self, worker_id: str):
        with self.uow() as repo:
            config = repo.get("sync_auto_config", CONFIG_ID, lock=True)
            if config is None or config["worker_id"] != worker_id:
                return
            repo.update(
                "sync_auto_config",
                CONFIG_ID,
                {"worker_id": None, "heartbeat_at": None, "updated_at": now()},
            )

    def renew(self, worker_id: str):
        current = now()
        with self.uow() as repo:
            config = repo.get("sync_auto_config", CONFIG_ID, lock=True)
            if config is None or config["lease_owner"] != worker_id:
                return False
            repo.update(
                "sync_auto_config",
                CONFIG_ID,
                {
                    "worker_id": worker_id,
                    "heartbeat_at": current,
                    "lease_expires_at": current + timedelta(seconds=self.lease_seconds),
                    "updated_at": current,
                },
            )
            return True

    def complete(self, worker_id: str, error_code: str | None):
        current = now()
        with self.uow() as repo:
            config = repo.get("sync_auto_config", CONFIG_ID, lock=True)
            if config is None or config["lease_owner"] != worker_id:
                return False
            repo.update(
                "sync_auto_config",
                CONFIG_ID,
                {
                    "heartbeat_at": current,
                    "lease_owner": None,
                    "lease_expires_at": None,
                    "last_auto_run_at": current,
                    "last_error_code": error_code,
                    "next_run_at": (
                        current + timedelta(seconds=config["interval_seconds"])
                        if config["enabled"]
                        else None
                    ),
                    "updated_at": current,
                },
            )
            return True
