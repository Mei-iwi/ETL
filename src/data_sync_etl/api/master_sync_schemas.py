from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SyncStream = Literal["all", "resources", "users", "grades", "subjects"]

SYNC_ERRORS = {
    'BLOCKED_SOURCE_IDENTITY': (409, 'SOURCE và MASTER có cùng ID nhưng khác thời điểm tạo. Cần đối soát nguồn đã được tạo lại trước khi đồng bộ.'),
    'BLOCKED_SOURCE_OWNERSHIP': (409, 'Bản ghi MASTER không thuộc nguồn PostgreSQL hiện tại. Cần đối soát nguồn sở hữu trước khi đồng bộ.'),
    'BLOCKED_LOCAL_SOURCE_CHANGE': (409, 'Bản ghi nguồn trong MASTER đã bị sửa cục bộ. Cần đối soát thay đổi trước khi đồng bộ.'),
    'BLOCKED_MONGO_ID_MAPPING': (409, 'Ánh xạ MongoDB của bản ghi nguồn không nhất quán. Cần đối soát ID trước khi đồng bộ.'),
    'BLOCKED_SOURCE_MAPPING': (503, 'Không đọc được SOURCE. Kiểm tra kết nối, chứng thư SSL, quyền đọc và schema nguồn.'),
}


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
    sync_mode: Literal["INCREMENTAL", "FULL"]


class SourceBackendStatus(BaseModel):
    status: str
    error_code: str | None = None


class MasterSyncStatusResponse(BaseModel):
    file_sync_supported: bool = False
    file_sync_error: str | None = "BLOCKED_SOURCE_STORAGE"
    file_worker_healthy: bool = False
    file_backends: dict[str, SourceBackendStatus] = Field(default_factory=dict)
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

    enabled: bool | None = None
    stream: SyncStream = "all"
    sync_mode: Literal["INCREMENTAL", "FULL"] | None = None

    @model_validator(mode="after")
    def require_update(self):
        if self.enabled is None and self.sync_mode is None:
            raise ValueError("Provide enabled or sync_mode")
        return self


class SourceSyncFile(BaseModel):
    source_version_id: str
    resource_id: str
    resource_title: str | None
    source_version_number: int
    source_lifecycle_status: str
    updated_at: datetime
    # UNTRACKED is the metadata view before worker discovery; other states are durable.
    import_status: Literal["UNTRACKED", "PENDING", "FETCHING", "IMPORTED", "PROCESSING", "RETRY_WAIT",
                           "RETRY_PROCESSING", "FAILED", "RETRY_EXHAUSTED"] = "UNTRACKED"
    retry_eligible: bool = False
    import_job_id: str | None = None
    etl_status: str | None = None
    retry_count: int | None = None
    last_error_code: str | None = None
    target_version_id: str | None = None
    processing_job_id: str | None = None


class SourceSyncFilesResponse(BaseModel):
    items: list[SourceSyncFile]
    next_cursor: str | None
    file_sync_supported: bool = False
    file_sync_error: str | None = "BLOCKED_SOURCE_STORAGE"
    file_worker_healthy: bool = False
    file_backends: dict[str, SourceBackendStatus] = Field(default_factory=dict)


class SourceFileRetryResponse(BaseModel):
    import_job_id: str
    action: Literal["QUEUED"]


class SourceFilesBatchRetryResponse(BaseModel):
    accepted_count: int
    skipped_count: int
    conflict_count: int
    next_cursor: str | None

