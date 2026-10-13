from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

SyncStream = Literal["all", "resources", "users", "grades", "subjects"]


class MasterSyncRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    stream: SyncStream = "all"
    full: bool = False


class MasterSyncRunResult(BaseModel):
    run_id: str
    records_read: int
    inserted: int
    updated: int
    skipped: int
    failed: int


class MasterSyncRunStatus(MasterSyncRunResult):
    stream_name: SyncStream
    sync_mode: Literal["FULL", "INCREMENTAL"] | None = None
    status: Literal["RUNNING", "COMPLETED", "FAILED"]
    started_at: datetime
    finished_at: datetime | None = None
    error_message: str | None = None


class MasterSyncAutoStream(BaseModel):
    stream_name: Literal["resources", "users", "grades", "subjects"]
    enabled: bool
    sync_mode: Literal["INCREMENTAL"]


class MasterSyncStatusResponse(BaseModel):
    auto_supported: bool
    auto_enabled: bool
    interval_seconds: int
    worker_healthy: bool
    auto_running: bool
    last_auto_run_at: datetime | None = None
    next_run_at: datetime | None = None
    auto_error: str | None = None
    auto_all_enabled: bool
    auto_streams: list[MasterSyncAutoStream]
    runs: list[MasterSyncRunStatus]


class MasterSyncAutoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enabled: bool
    stream: SyncStream = "all"

