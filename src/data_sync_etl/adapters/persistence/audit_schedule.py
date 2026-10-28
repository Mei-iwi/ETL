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
        # One lock covers both configured and fallback decisions across workers.
        with self.uow.processing_lock('reconciliation-scheduler') as acquired:
            return self._tick() if acquired else []

    def _tick(self):
        queue = AuditQueue(self.uow, self.settings)
        with self.uow() as repo:
            configured = {row.stream_name: dict(row.checkpoint or {}) for row in repo.session.scalars(
                select(Job).where(Job.status == 'CONFIGURED', Job.job_key.startswith('schedule:')))}
        decisions = []
        for stream, plan in configured.items():
            if not plan['enabled']:
                continue
            kind = ('DEEP' if plan['mode'] == 'FULL' else 'LIGHT') if stream == 'files' else 'METADATA'
            decisions.append((kind, stream, plan['interval_seconds'], plan['mode'], plan['repair'], 'configured'))
        if self.settings.reconciliation_scheduler_enabled:
            for kind, interval in [('METADATA', self.settings.reconciliation_light_interval_seconds),
                                   ('IDS', self.settings.reconciliation_id_sweep_interval_seconds),
                                   ('LIGHT', self.settings.reconciliation_light_interval_seconds), ('DEEP', 86400)]:
                for stream in ('files',) if kind in ('LIGHT', 'DEEP') else SCOPES[:-1]:
                    plan = configured.get(stream)
                    if plan and kind != 'IDS':
                        selected = ('DEEP' if plan['mode'] == 'FULL' else 'LIGHT') if stream == 'files' else 'METADATA'
                        if not plan['enabled'] or kind == selected:
                            continue
                    decisions.append((kind, stream, interval, None, False, kind))
        jobs = []
        for kind, stream, interval, mode, repair, prefix in decisions:
            with self.uow() as repo:
                active = repo.session.scalar(select(Job.id).where(Job.stream_name == stream, Job.audit_type == kind,
                    Job.status.in_(('PENDING', 'RUNNING', 'RETRY_WAIT'))).limit(1))
            if active:
                jobs.append(active)
                continue
            slot = int(now().timestamp() // interval)
            jobs.append(queue.enqueue(kind, stream, f'{prefix}:{stream}:{interval}:{slot}', mode=mode, repair=repair))
        return jobs
