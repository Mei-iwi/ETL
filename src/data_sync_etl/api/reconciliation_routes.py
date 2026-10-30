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

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select

from data_sync_etl.adapters.persistence.audit_queue import utc
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit, recent_file_evidence
from data_sync_etl.api.admin_access import require_local_admin
from data_sync_etl.api.reconciliation_schemas import (
    AllAuditJobRequest,
    AuditJobRequest,
    AuditScheduleRequest,
    DiscrepancyItemResponse,
    DiscrepancyListResponse,
    ReconcileTriggerRequest,
    ReconcileTriggerResponse,
    ReconciliationLatestReport,
    ReconciliationRunReport,
)
from data_sync_etl.application.reconciliation_engine import serialize_json_value
from data_sync_etl.db.models import (
    MasterLearningResource,
    ReconciliationAuditJob,
    ReconciliationFileState,
    ResourceVersion,
    SyncReconciliationDiscrepancy,
    SyncReconciliationRun,
)
from data_sync_etl.domain.core import DomainError, now


def reconciliation_admin(request: Request):
    return require_local_admin(request)


router = APIRouter(prefix="/api/v1/reconciliation", tags=["Reconciliation & Integrity Verification"],
                   dependencies=[Depends(reconciliation_admin)])


@router.post('/audit-jobs', status_code=202)
def enqueue_audit(request: Request, body: AuditJobRequest):
    container = request.app.state.container
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    try:
        if body.repair and body.stream == 'files':
            raise DomainError('INVALID_AUDIT_SCOPE')
        job_id = audit.queue.enqueue(body.audit_type, body.stream, mode=body.mode, repair=body.repair)
    except DomainError:
        raise HTTPException(422, 'INVALID_AUDIT_SCOPE') from None
    return {'job_id': job_id, 'status': 'PENDING', 'audit_type': body.audit_type,
            'stream': body.stream, 'worker_required': True}


@router.get('/schedules')
def audit_schedules(request: Request):
    from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
    container = request.app.state.container
    return {'items': AuditSchedule(container.uow, container.settings).list(), 'worker_required': True}


@router.post('/audit-jobs/all', status_code=202)
def enqueue_all_audits(request: Request, body: AllAuditJobRequest):
    container = request.app.state.container
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    jobs = audit.queue.enqueue_all(mode=body.mode, repair=body.repair)
    return {'status': 'PENDING', 'items': jobs, 'worker_required': True}


@router.put('/schedules/{stream}')
def configure_audit_schedule(request: Request, stream: str, body: 'AuditScheduleRequest'):
    from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
    container = request.app.state.container
    try:
        AuditSchedule(container.uow, container.settings).configure(stream, body.model_dump(exclude_unset=True))
    except ValueError as error:
        if str(error) == 'AUDIT_SCHEDULE_BUSY':
            raise HTTPException(409, 'Lịch đang được cập nhật; vui lòng thử lưu lại.') from None
        raise HTTPException(422, 'INVALID_AUDIT_SCOPE') from None
    return {'status': 'CONFIGURED', 'worker_required': True}


@router.get('/audit-jobs')
def audit_jobs(request: Request, after: str | None = None, size: int = Query(20, ge=1, le=100),
               stream: str | None = None, newest: bool = False,
               job_ids: list[str] | None = Query(None, max_length=5)):  # noqa: B008
    with request.app.state.container.uow() as repo:
        # SOURCE write-back requests reuse the durable job primitive but have a
        # separate API/status surface; they are not reconciliation audit jobs.
        query = select(ReconciliationAuditJob).where(
            ReconciliationAuditJob.status != 'CONFIGURED',
            ReconciliationAuditJob.audit_type != 'SOURCE_WRITEBACK',
        )
        if stream:
            query = query.where(ReconciliationAuditJob.stream_name == stream)
        if job_ids:
            query = query.where(ReconciliationAuditJob.id.in_(job_ids))
        query = query.order_by(ReconciliationAuditJob.id.desc() if newest else ReconciliationAuditJob.id).limit(size + 1)
        if after:
            query = query.where(ReconciliationAuditJob.id < after if newest else ReconciliationAuditJob.id > after)
        rows = list(repo.session.scalars(query))
        return {'items': [{'job_id': r.id, 'audit_type': r.audit_type, 'stream': r.stream_name,
            'status': r.status, 'error_code': r.error_code, 'progress': r.checkpoint,
            'created_at': r.created_at, 'next_retry_at': r.next_retry_at} for r in rows[:size]],
            'next_cursor': rows[size - 1].id if len(rows) > size else None}


@router.get('/coverage')
def audit_coverage(request: Request):
    container = request.app.state.container
    return ScheduledAudit(container.reconciliation_engine, container.settings).coverage()


@router.get('/file-evidence/{version_id}')
def file_evidence(request: Request, version_id: str):
    with request.app.state.container.uow() as repo:
        row = repo.session.get(ReconciliationFileState, version_id)
        if row is None:
            return {'resource_version_id': version_id, 'status': 'NOT_VERIFIED'}
        version = repo.session.get(ResourceVersion, version_id)
        resource = repo.session.get(MasterLearningResource, version.resource_id) if version else None
        recent = bool(resource and recent_file_evidence(row, version, resource.source_origin,
            request.app.state.container.settings))
        stale = row.status == 'SYNCED' and not recent
        return {'resource_version_id': version_id, 'status': 'NOT_VERIFIED' if stale else row.status,
            'reason_code': 'AUDIT_EVIDENCE_STALE' if stale else row.reason_code,
            'recent_sha256_evidence': recent,
            'last_verified_at': row.last_verified_at, 'verification_method': row.verification_method,
            'last_verified_size': row.last_verified_size, 'next_due_at': row.next_due_at}


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
    container = request.app.state.container
    if container.settings.source_federation_enabled:
        result = container.reconcile_master.run(
            body.stream, apply=not body.dry_run, fingerprint=body.fingerprint, source_id=body.source_id)
        reports = []
        for stream_name, item in result.get('streams', {}).items():
            source_count = item['source_count']
            target_count = item['eligible_master_count']
            missing = item['missing_candidate_count']
            reports.append({
                'run_id': result.get('run_id', 'preview'), 'stream_name': stream_name,
                'status': result['status'], 'mode': body.mode, 'execution_time_ms': 0,
                'started_at': result.get('previewed_at', now().isoformat()),
                'finished_at': result.get('detected_at'), 'source_count': source_count,
                'target_count': target_count, 'matched_count': max(0, min(source_count, target_count) - missing),
                'mismatched_count': missing,
                'match_rate_percent': round(100 * max(0, min(source_count, target_count) - missing)
                                            / max(1, source_count), 2),
                'summary_meta': item,
            })
        return {
            'status': result['status'], 'executed_at': result.get('detected_at', result.get('previewed_at', now().isoformat())),
            'dry_run': body.dry_run, 'mode': body.mode, 'execution_time_ms': 0,
            'total_streams': len(reports), 'reports': reports,
            'source_id': result.get('source_id'), 'fingerprint': result.get('fingerprint'),
            'candidate_count': result.get('candidate_count'),
            'sample_master_ids': result.get('sample_master_ids', []),
        }
    if not body.dry_run:
        if body.stream in ('files', 'all'):
            raise HTTPException(409, 'Chọn riêng một luồng metadata để đồng bộ bản ghi lệch; tệp sai hash cần xử lý tại nguồn.')
        return container.reconciled_sync.run(body.stream, full=body.mode == 'FULL')
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
        if str(exc) in ('SOURCE_UNAVAILABLE', 'SOURCE_METADATA_UNSUPPORTED'):
            raise HTTPException(503, 'SOURCE_AUDIT_UNAVAILABLE') from None
        raise
    return result


@router.get("/runs/latest", response_model=ReconciliationLatestReport)
def get_latest_reconciliation_report(
    request: Request,
    stream: str | None = Query(None, description="Lọc theo luồng dữ liệu cụ thể"),
    run_ids: list[str] | None = Query(None, max_length=5),  # noqa: B008
) -> dict[str, Any]:
    """Lấy báo cáo tổng hợp và Dashboard KPI của đợt đối soát mới nhất."""
    container = request.app.state.container
    with container.uow() as repo:
        session = repo.session

        # Truy vấn các lần chạy gần nhất theo từng stream
        streams = [stream] if stream else ["grades", "subjects", "users", "resources", "files"]
        latest_runs: list[SyncReconciliationRun] = []

        for st in streams:
            query = select(SyncReconciliationRun).where(SyncReconciliationRun.stream_name == st)
            if run_ids:
                query = query.where(SyncReconciliationRun.run_id.in_(run_ids))
            run = (
                session.scalars(
                    query
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

        def source_count(run):
            meta = run.summary_meta or {}
            scanned = meta.get('records_scanned')
            if run.stream_name == 'files' and meta.get('audit_type') in ('LIGHT', 'DEEP') and type(scanned) is int and scanned >= 0:
                return scanned + (meta.get('cycle_records_scanned', 0) if meta.get('manual_full_scan') else 0)
            return run.source_count

        total_source = sum(source_count(r) for r in latest_runs)
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
            if run.status == 'RUNNING' and (run.summary_meta or {}).get('audit_type'):
                job = session.get(ReconciliationAuditJob, run.run_id)
                active = (job and job.status == 'RUNNING' and job.lease_expires_at
                          and utc(job.lease_expires_at) > now())
                return 'RUNNING' if active else 'PARTIAL'
            return 'PARTIAL' if recoverable and run.stream_name == 'files' and run.status == 'RUNNING' else run.status
        overall_status = next((status for status in ('RUNNING', 'FAILED', 'BLOCKED', 'PARTIAL')
                               if any(displayed_status(run) == status for run in latest_runs)), 'COMPLETED')

        stream_reports = []
        for r in latest_runs:
            pop = max(1, max(source_count(r), r.target_count))
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
                    source_count=source_count(r),
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
    run_ids: list[str] | None = Query(None, max_length=5),  # noqa: B008
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
        if run_ids:
            query = query.where(SyncReconciliationDiscrepancy.run_id.in_(run_ids))
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
