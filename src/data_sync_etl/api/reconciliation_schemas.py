from __future__ import annotations

from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.domain.audit_calendar import DEFAULT_TIMEZONE


class ReconcileTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream: Literal["all", "resources", "subjects", "grades", "users", "files"] = Field(
        default="all",
        description="Tên luồng đối soát cần thực hiện",
    )
    dry_run: bool = Field(
        default=True,
        description="True: chỉ báo cáo; False: chỉ đồng bộ metadata lệch của một luồng, không tự xóa/tải lại tệp",
    )
    mode: Literal["INCREMENTAL", "FULL"] = Field(
        default="INCREMENTAL",
        description="Chế độ đối soát: INCREMENTAL (chỉ file mới/thay đổi) hoặc FULL (toàn bộ kho)",
    )
    source_id: str | None = Field(default=None, min_length=1, max_length=30)
    fingerprint: str | None = Field(default=None, min_length=64, max_length=64)


class AllAuditJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['INCREMENTAL', 'FULL'] = 'INCREMENTAL'
    repair: bool = False


class AuditJobRequest(AllAuditJobRequest):
    audit_type: Literal['LIGHT', 'DEEP', 'METADATA', 'IDS'] = 'DEEP'
    stream: Literal['files', 'resources', 'subjects', 'grades', 'users'] = 'files'


class CalendarAuditSchedule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    cadence: Literal['INTERVAL', 'DAILY', 'WEEKLY', 'MONTHLY'] = 'DAILY'
    hour: int = Field(default=2, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weekday: int = Field(default=6, ge=0, le=6)
    month_day: int = Field(default=1, ge=1, le=28)
    timezone: str = Field(default=DEFAULT_TIMEZONE, min_length=1, max_length=64)
    interval_seconds: Literal[60, 300, 600, 900, 1200, 1800, 3600] = 1800
    repair: bool = False

    @model_validator(mode='after')
    def valid_timezone(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('INVALID_AUDIT_TIMEZONE') from None
        return self


class AuditScheduleRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    interval_seconds: Literal[60, 300, 600, 900, 1200, 1800, 3600] = 1800
    mode: Literal['INCREMENTAL', 'FULL'] = 'INCREMENTAL'
    repair: bool = False
    fast_schedule: CalendarAuditSchedule | None = None
    thorough_schedule: CalendarAuditSchedule | None = None

    @model_validator(mode='after')
    def no_null_schedule(self):
        if any(key in self.model_fields_set and getattr(self, key) is None
               for key in ('fast_schedule', 'thorough_schedule')):
            raise ValueError('INVALID_AUDIT_SCHEDULE')
        return self


class ReconciliationRunReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    run_id: str
    stream_name: str
    status: str
    mode: str = "INCREMENTAL"
    execution_time_ms: float = 0.0
    started_at: str
    finished_at: str | None = None
    source_count: int
    target_count: int
    matched_count: int
    mismatched_count: int
    match_rate_percent: float
    source_total_bytes: int | None = 0
    target_total_bytes: int | None = 0
    source_table_hash: str | None = None
    target_table_hash: str | None = None
    discrepancies_sample: list[dict[str, Any]] = Field(default_factory=list)
    summary_meta: dict[str, Any] | None = None


class ReconcileTriggerResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: str
    executed_at: str
    dry_run: bool
    mode: str = "INCREMENTAL"
    execution_time_ms: float = 0.0
    total_streams: int
    reports: list[ReconciliationRunReport]
    source_id: str | None = None
    fingerprint: str | None = None
    candidate_count: int | None = None
    sample_master_ids: list[str] = Field(default_factory=list)


class DiscrepancyItemResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    run_id: str
    record_id: str
    stream_name: str
    discrepancy_flag: str
    source_value: dict[str, Any] | None = None
    target_value: dict[str, Any] | None = None
    detected_at: str
    resolution_status: str


class DiscrepancyListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    items: list[DiscrepancyItemResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class ReconciliationLatestReport(BaseModel):
    model_config = ConfigDict(extra="ignore")
    latest_run_id: str | None = None
    last_reconciled_at: str | None = None
    overall_status: str
    overall_match_rate_percent: float
    total_source_records: int
    total_target_records: int
    total_matched_records: int
    total_mismatched_records: int
    total_source_bytes: int
    total_target_bytes: int
    stream_reports: list[ReconciliationRunReport]
