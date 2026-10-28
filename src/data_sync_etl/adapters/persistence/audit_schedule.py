"""Admin schedules in existing audit control rows, without business schema changes."""
from sqlalchemy import select, update

from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.domain.core import new_id, now

SCOPES = ('grades', 'subjects', 'users', 'resources', 'files')
INTERVALS = (60, 300, 600, 900, 1200, 1800, 3600)


class AuditSchedule:
    def __init__(self, uow, settings):
        self.uow, self.settings = uow, settings

    def list(self):
        with self.uow() as repo:
            rows = {row.stream_name: dict(row.checkpoint or {}) for row in repo.session.scalars(
                select(Job).where(Job.status == 'CONFIGURED', Job.job_key.startswith('schedule:')))}
        return [{'stream': stream, 'enabled': False, 'interval_seconds': 1800,
                 'mode': 'INCREMENTAL', 'repair': stream != 'files', **rows.get(stream, {})} for stream in SCOPES]

    def configure(self, stream, values):
        if stream not in (*SCOPES, 'all') or values['interval_seconds'] not in INTERVALS:
            raise ValueError('INVALID_AUDIT_SCHEDULE')
        with self.uow() as repo:
            for target in SCOPES if stream == 'all' else (stream,):
                row = repo.session.scalar(select(Job).where(Job.job_key == f'schedule:{target}').with_for_update())
                if row is None:
                    row = Job(id=new_id(), job_key=f'schedule:{target}', audit_type='LIGHT' if target == 'files' else 'METADATA',
                              stream_name=target, status='CONFIGURED', checkpoint={})
                    repo.session.add(row)
                row.checkpoint = {**values, 'repair': values['repair'] and target != 'files'}
                if not values['enabled']:
                    repo.session.execute(update(Job).where(Job.stream_name == target,
                        Job.job_key.startswith(f'configured:{target}:'), Job.status.in_(('PENDING', 'RETRY_WAIT')))
                        .values(status='CANCELLED', finished_at=now(), error_code='CANCELLED_BY_ADMIN'))

    def tick(self):
        queue = AuditQueue(self.uow, self.settings)
        for plan in self.list():
            if not plan['enabled']:
                continue
            kind = ('DEEP' if plan['mode'] == 'FULL' else 'LIGHT') if plan['stream'] == 'files' else 'METADATA'
            with self.uow() as repo:
                active = repo.session.scalar(select(Job.id).where(Job.stream_name == plan['stream'],
                    Job.status.in_(('PENDING', 'RUNNING', 'RETRY_WAIT'))).limit(1))
            if active:
                continue
            slot = int(now().timestamp() // plan['interval_seconds'])
            queue.enqueue(kind, plan['stream'], f"configured:{plan['stream']}:{plan['interval_seconds']}:{slot}",
                          mode=plan['mode'], repair=plan['repair'])
