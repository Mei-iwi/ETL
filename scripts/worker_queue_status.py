"""Read-only queue/lease/backlog snapshot; never emits connection strings or content.

Run from the configured checkout: python -m scripts.worker_queue_status
Machine metrics are optional when psutil is already installed.
"""
import json
from datetime import timedelta

from sqlalchemy import func, select, text

from data_sync_etl.config import Settings
from data_sync_etl.db.models import OcrPageTask, ReconciliationAuditJob, SourceFileImport
from data_sync_etl.db.session import make_engine
from data_sync_etl.domain.core import now


def snapshot(engine, settings):
    stamp = now()
    result = {'at': stamp.isoformat(), 'capacity': {
        'source_import': settings.source_file_import_max_active,
        'audit': settings.reconciliation_max_active}, 'queues': {}}
    with engine.connect() as connection:
        if engine.dialect.name == 'postgresql':
            connection.execute(text('SET TRANSACTION READ ONLY'))
        for name, model, active, lease in (
                ('source_import', SourceFileImport, 'FETCHING', SourceFileImport.lease_expires_at),
                ('audit', ReconciliationAuditJob, 'RUNNING', ReconciliationAuditJob.lease_expires_at),
                ('ocr', OcrPageTask, 'RUNNING', OcrPageTask.heartbeat_at)):
            expiry = stamp - timedelta(seconds=settings.ocr_heartbeat_timeout_seconds) if name == 'ocr' else stamp
            groups = connection.execute(select(model.status, func.count(), func.min(model.created_at))
                                        .group_by(model.status)).all()
            result['queues'][name] = {
                'statuses': {status: {'count': count, 'oldest_created_at': oldest.isoformat() if oldest else None}
                             for status, count, oldest in groups},
                'unexpired_active': connection.scalar(select(func.count()).select_from(model)
                    .where(model.status == active, lease > expiry)),
                'expired_active': connection.scalar(select(func.count()).select_from(model)
                    .where(model.status == active, lease <= expiry)),
            }
        result['audit_finished_last_minute'] = connection.scalar(select(func.count()).select_from(
            ReconciliationAuditJob).where(ReconciliationAuditJob.finished_at >= stamp - timedelta(minutes=1)))
        if engine.dialect.name == 'postgresql':
            result['postgres'] = {'connections': [dict(row) for row in connection.execute(text(
                'SELECT state, wait_event_type, wait_event, count(*) AS count FROM pg_stat_activity '
                'WHERE datname=current_database() GROUP BY state, wait_event_type, wait_event')).mappings()],
                'max_connections': connection.scalar(text("SELECT current_setting('max_connections')::int"))}
    return result


def main():
    settings = Settings()
    engine = make_engine(settings.database_url)
    try:
        result = snapshot(engine, settings)
        try:
            import psutil
            result['machine'] = {'cpu_percent': psutil.cpu_percent(interval=.1),
                                 'available_memory_bytes': psutil.virtual_memory().available,
                                 'snapshot_process_rss_bytes': psutil.Process().memory_info().rss}
        except ImportError:
            result['machine'] = None
        print(json.dumps(result, indent=2))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
