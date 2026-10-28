from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class AllAuditJobRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mode: Literal['INCREMENTAL', 'FULL'] = 'INCREMENTAL'
    repair: bool = False


class AuditJobRequest(AllAuditJobRequest):
    audit_type: Literal['LIGHT', 'DEEP', 'METADATA', 'IDS'] = 'DEEP'
    stream: Literal['files', 'resources', 'subjects', 'grades', 'users'] = 'files'


class AuditScheduleRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    interval_seconds: Literal[60, 300, 600, 900, 1200, 1800, 3600] = 1800
    mode: Literal['INCREMENTAL', 'FULL'] = 'INCREMENTAL'
    repair: bool = False


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
