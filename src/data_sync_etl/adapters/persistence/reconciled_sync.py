"""SQLAlchemy adapter for bounded metadata reconciliation repairs."""
from sqlalchemy import select

from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationAuditJob, SyncReconciliationRun
from data_sync_etl.domain.core import now


class SQLAlchemyReconciledSync:
    def __init__(self, engine, sync, settings):
        self.engine, self.sync, self.settings = engine, sync, settings

    def run(self, stream, full=False):
        audit = ScheduledAudit(self.engine, self.settings, self.sync)
        with self.engine.uow() as repo:
            job_id = repo.session.scalar(select(ReconciliationAuditJob.id).where(
                ReconciliationAuditJob.audit_type == 'METADATA', ReconciliationAuditJob.stream_name == stream,
                ReconciliationAuditJob.status.in_(('PENDING', 'RUNNING', 'RETRY_WAIT')))
                .order_by(ReconciliationAuditJob.created_at).limit(1))
        if job_id is None:
            job_id = audit.queue.enqueue('METADATA', stream, mode='FULL' if full else 'INCREMENTAL', repair=True)
        audit.once(job_id)
        with self.engine.uow() as repo:
            job = repo.session.get(ReconciliationAuditJob, job_id)
            run = repo.session.get(SyncReconciliationRun, job_id)
            mode = (job.checkpoint or {}).get('mode', 'FULL' if full else 'INCREMENTAL')
            reports = [] if run is None else [dict(run_id=run.run_id, stream_name=stream,
                status=run.status, mode=mode, execution_time_ms=0,
                started_at=run.started_at.isoformat(), finished_at=run.finished_at.isoformat() if run.finished_at else None,
                source_count=run.source_count, target_count=run.target_count, matched_count=run.matched_count,
                mismatched_count=run.mismatched_count,
                match_rate_percent=round(100 * run.matched_count / max(1, run.source_count), 2),
                summary_meta=run.summary_meta)]
            return dict(status=job.status, executed_at=now().isoformat(),
                dry_run=not (job.checkpoint or {}).get('repair_requested', False),
                mode=mode, execution_time_ms=0, total_streams=1, reports=reports)
