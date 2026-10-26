"""REST API Endpoints cho Hệ thống Đối soát & Kiểm tra Toàn vẹn Dữ liệu.

Prefix: /api/v1/reconciliation
Các API:
- POST /runs: Kích hoạt đợt đối soát mới (dry_run, stream)
- GET /runs/latest: Dashboard KPI đối soát mới nhất
- GET /discrepancies: Danh sách chi tiết bản ghi lệch (hỗ trợ lọc và phân trang)
"""

from __future__ import annotations

import math
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select

from data_sync_etl.api.reconciliation_schemas import (
    DiscrepancyItemResponse,
    DiscrepancyListResponse,
    ReconcileTriggerRequest,
    ReconcileTriggerResponse,
    ReconciliationLatestReport,
    ReconciliationRunReport,
)
from data_sync_etl.application.reconciliation_engine import serialize_json_value
from data_sync_etl.db.models import (
    SyncReconciliationDiscrepancy,
    SyncReconciliationRun,
)
from data_sync_etl.domain.core import DomainError

router = APIRouter(prefix="/api/v1/reconciliation", tags=["Reconciliation & Integrity Verification"])


@router.post("/runs", response_model=ReconcileTriggerResponse,
             responses={202: {'model': ReconcileTriggerResponse, 'description': 'Đã tiếp nhận tác vụ đối soát nền'}})
def trigger_reconciliation(
    request: Request,
    response: Response,
    body: ReconcileTriggerRequest | None = None,
) -> dict[str, Any]:
    """Kích hoạt một đợt đối soát toàn vẹn dữ liệu giữa Nguồn và Đích.

    - Kiểm tra kiểm soát dung lượng file vật lý 2 cấp (Fast check O(1) + Deep check O(N)).
    - Tính Table Fingerprint qua hàm băm SQL gộp trên PostgreSQL.
    - So sánh song song và phát hiện các loại cờ lỗi: ORPHAN_SOURCE, ORPHAN_MASTER, DESYNC_BLOB_SIZE, DESYNC_CHECKSUM, DESYNC_METADATA.
    - Tự động áp dụng ngưỡng an toàn max_ratio <= 10% (0.1).
    """
    body = body or ReconcileTriggerRequest()
    engine = request.app.state.container.reconciliation_engine
    if not body.dry_run:
        raise HTTPException(409, 'Đối soát chỉ phát hiện sai lệch. Dùng chức năng đồng bộ để cập nhật dữ liệu.')
    try:
        if body.stream in ('files', 'all'):
            result = request.app.state.container.reconciliation_jobs.start(body.stream, mode=body.mode)
            result.setdefault('mode', body.mode)
            result.setdefault('execution_time_ms', 0)
            response.status_code = 202
            return result
        result = engine.execute_stream_reconcile(stream_name=body.stream, dry_run=body.dry_run, mode=body.mode)
    except DomainError as exc:
        if str(exc) == 'RECONCILIATION_BUSY':
            raise HTTPException(409, 'Một đợt đối soát đang chạy. Vui lòng chờ hoàn tất.') from None
        if str(exc) == 'RECONCILIATION_LIMIT_EXCEEDED':
            raise HTTPException(422, 'Luồng dữ liệu vượt giới hạn 10.000 bản ghi; cần đối soát theo lô riêng.') from None
        if str(exc) in ('RECONCILIATION_START_TIMEOUT', 'RECONCILIATION_FAILED'):
            raise HTTPException(503, 'Không khởi động được đối soát nền. Kiểm tra kết nối Master và bảng audit.') from None
        raise
    return result


@router.get("/runs/latest", response_model=ReconciliationLatestReport)
def get_latest_reconciliation_report(
    request: Request,
    stream: str | None = Query(None, description="Lọc theo luồng dữ liệu cụ thể"),
) -> dict[str, Any]:
    """Lấy báo cáo tổng hợp và Dashboard KPI của đợt đối soát mới nhất."""
    container = request.app.state.container
    with container.uow() as repo:
        session = repo.session

        # Truy vấn các lần chạy gần nhất theo từng stream
        streams = [stream] if stream else ["grades", "subjects", "users", "resources", "files"]
        latest_runs: list[SyncReconciliationRun] = []

        for st in streams:
            run = (
                session.scalars(
                    select(SyncReconciliationRun)
                    .where(SyncReconciliationRun.stream_name == st)
                    .order_by(SyncReconciliationRun.started_at.desc())
                    .limit(1)
                )
                .first()
            )
            if run:
                latest_runs.append(run)

        if not latest_runs:
            return {
                "latest_run_id": None,
                "last_reconciled_at": None,
                "overall_status": "NO_RUNS_YET",
                "overall_match_rate_percent": 0.0,
                "total_source_records": 0,
                "total_target_records": 0,
                "total_matched_records": 0,
                "total_mismatched_records": 0,
                "total_source_bytes": 0,
                "total_target_bytes": 0,
                "stream_reports": [],
            }

        total_source = sum(r.source_count for r in latest_runs)
        total_target = sum(r.target_count for r in latest_runs)
        total_matched = sum(r.matched_count for r in latest_runs)
        total_mismatched = sum(r.mismatched_count for r in latest_runs)
        total_source_bytes = sum(r.source_total_bytes or 0 for r in latest_runs)
        total_target_bytes = sum(r.target_total_bytes or 0 for r in latest_runs)

        max_pop = max(1, max(total_source, total_target))
        overall_match_rate = round((total_matched / max_pop) * 100, 2)
        latest_runs.sort(key=lambda run: (run.started_at, run.run_id), reverse=True)
        recoverable = (any(r.stream_name == 'files' and r.status == 'RUNNING' for r in latest_runs)
                       and container.reconciliation_jobs.can_recover())
        def displayed_status(run):
            return 'PARTIAL' if recoverable and run.stream_name == 'files' and run.status == 'RUNNING' else run.status
        overall_status = next((status for status in ('RUNNING', 'FAILED', 'BLOCKED', 'PARTIAL')
                               if any(displayed_status(run) == status for run in latest_runs)), 'COMPLETED')

        stream_reports = []
        for r in latest_runs:
            pop = max(1, max(r.source_count, r.target_count))
            match_pct = round((r.matched_count / pop) * 100, 2)
            meta = r.summary_meta or {}
            run_mode = meta.get("mode", "INCREMENTAL")
            run_exec_ms = meta.get("execution_time_ms", 0.0)
            stream_reports.append(
                ReconciliationRunReport(
                    run_id=r.run_id,
                    stream_name=r.stream_name,
                    status=displayed_status(r),
                    mode=run_mode,
                    execution_time_ms=run_exec_ms,
                    started_at=r.started_at.isoformat(),
                    finished_at=r.finished_at.isoformat() if r.finished_at else None,
                    source_count=r.source_count,
                    target_count=r.target_count,
                    matched_count=r.matched_count,
                    mismatched_count=r.mismatched_count,
                    match_rate_percent=match_pct,
                    source_total_bytes=r.source_total_bytes,
                    target_total_bytes=r.target_total_bytes,
                    source_table_hash=r.source_table_hash,
                    target_table_hash=r.target_table_hash,
                    summary_meta=({**meta, 'reason': 'RECONCILIATION_INTERRUPTED'}
                                  if displayed_status(r) != r.status else meta),
                )
            )

        return {
            "latest_run_id": latest_runs[0].run_id if latest_runs else None,
            "last_reconciled_at": latest_runs[0].started_at.isoformat() if latest_runs else None,
            "overall_status": overall_status,
            "overall_match_rate_percent": overall_match_rate,
            "total_source_records": total_source,
            "total_target_records": total_target,
            "total_matched_records": total_matched,
            "total_mismatched_records": total_mismatched,
            "total_source_bytes": total_source_bytes,
            "total_target_bytes": total_target_bytes,
            "stream_reports": stream_reports,
        }


@router.get("/discrepancies", response_model=DiscrepancyListResponse)
def list_discrepancies(
    request: Request,
    run_id: str | None = Query(None, description="Lọc theo mã đợt đối soát cụ thể"),
    latest_only: bool = Query(False, description="Chỉ lấy đợt mới nhất của từng luồng"),
    stream: str | None = Query(None, description="Lọc theo luồng dữ liệu (resources, files, subjects...)"),
    discrepancy_flag: str | None = Query(None, description="Lọc theo cờ hiệu (DESYNC_BLOB_SIZE, DESYNC_CHECKSUM, ORPHAN_SOURCE...)"),
    page: int = Query(1, ge=1, description="Số trang hiển thị (bắt đầu từ 1)"),
    page_size: int = Query(20, ge=1, le=1000, description="Số bản ghi trên mỗi trang"),
) -> dict[str, Any]:
    """Lấy danh sách chi tiết các bản ghi bị lệch để hiển thị bảng so sánh đối soát song song (Side-by-side reconciliation view)."""
    container = request.app.state.container
    with container.uow() as repo:
        session = repo.session

        query = select(SyncReconciliationDiscrepancy)
        if latest_only:
            ranked = select(SyncReconciliationRun.run_id, func.row_number().over(
                partition_by=SyncReconciliationRun.stream_name,
                order_by=(SyncReconciliationRun.started_at.desc(), SyncReconciliationRun.run_id.desc())
            ).label('ordinal')).subquery()
            query = query.where(SyncReconciliationDiscrepancy.run_id.in_(
                select(ranked.c.run_id).where(ranked.c.ordinal == 1)))
        if run_id:
            query = query.where(SyncReconciliationDiscrepancy.run_id == run_id)
        if stream:
            query = query.where(SyncReconciliationDiscrepancy.stream_name == stream)
        if discrepancy_flag:
            query = query.where(SyncReconciliationDiscrepancy.discrepancy_flag == discrepancy_flag)

        # Đếm tổng số bản ghi phù hợp điều kiện
        count_stmt = select(func.count()).select_from(query.subquery())
        total = session.scalar(count_stmt) or 0

        # Phân trang và sắp xếp
        offset = (page - 1) * page_size
        query = query.order_by(
            SyncReconciliationDiscrepancy.detected_at.desc(),
            SyncReconciliationDiscrepancy.id.desc(),
        ).offset(offset).limit(page_size)

        rows = session.scalars(query).all()

        items = [
            DiscrepancyItemResponse(
                id=r.id,
                run_id=r.run_id,
                record_id=r.record_id,
                stream_name=r.stream_name,
                discrepancy_flag=r.discrepancy_flag,
                source_value=serialize_json_value(r.source_value),
                target_value=serialize_json_value(r.target_value),
                detected_at=r.detected_at.isoformat(),
                resolution_status=r.resolution_status,
            )
            for r in rows
        ]

        total_pages = math.ceil(total / page_size) if total > 0 else 0

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }
