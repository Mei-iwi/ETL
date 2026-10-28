"""Admin schedules in existing audit control rows, without business schema changes."""
from datetime import timedelta

from sqlalchemy import or_, select, update

from data_sync_etl.adapters.persistence.audit_queue import AuditQueue, utc
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.domain.audit_calendar import INTERVALS, schedule_slot, suggested_schedule, validate_schedule
from data_sync_etl.domain.core import new_id, now

SCOPES = ('grades', 'subjects', 'users', 'resources', 'files')
BRANCHES = ('fast', 'thorough')


class AuditSchedule:
    def __init__(self, uow, settings):
        self.uow, self.settings = uow, settings

    def list(self):
        stamp = now()
        with self.uow() as repo:
            rows = {row.stream_name: dict(row.checkpoint or {}) for row in repo.session.scalars(
                select(Job).where(Job.status == 'CONFIGURED', Job.job_key.startswith('schedule:')))}
        result = []
        for stream in SCOPES:
            stored = rows.get(stream, {})
            plan = {'stream': stream, 'enabled': False, 'interval_seconds': 1800,
                    'mode': 'INCREMENTAL', 'repair': stream != 'files', **stored}
            plan['calendar_configured'] = any(f'{branch}_schedule' in stored for branch in BRANCHES)
            for branch in BRANCHES:
                key = f'{branch}_schedule'
                value = {**suggested_schedule(stream, branch), **stored.get(key, {})}
                value['next_run_at'] = schedule_slot(value, stamp)[1].isoformat() if value['enabled'] else None
                plan[key] = value
            result.append(plan)
        return result

    def configure(self, stream, values):
        if stream not in (*SCOPES, 'all') or set(values) - {
                'enabled', 'interval_seconds', 'mode', 'repair', 'fast_schedule', 'thorough_schedule'}:
            raise ValueError('INVALID_AUDIT_SCHEDULE')
        with self.uow.processing_lock('reconciliation-scheduler') as acquired:
            if not acquired:
                raise ValueError('AUDIT_SCHEDULE_BUSY')
            with self.uow() as repo:
                for target in SCOPES if stream == 'all' else (stream,):
                    row = repo.session.scalar(select(Job).where(Job.job_key == f'schedule:{target}').with_for_update())
                    if row is None:
                        row = Job(id=new_id(), job_key=f'schedule:{target}',
                                  audit_type='LIGHT' if target == 'files' else 'METADATA',
                                  stream_name=target, status='CONFIGURED', checkpoint={})
                        repo.session.add(row)
                    previous_config = dict(row.checkpoint or {})
                    merged = {'enabled': False, 'interval_seconds': 1800, 'mode': 'INCREMENTAL',
                              'repair': False, **previous_config}
                    for key, value in values.items():
                        if key.endswith('_schedule'):
                            branch = key.removesuffix('_schedule')
                            if not isinstance(value, dict):
                                raise ValueError('INVALID_AUDIT_SCHEDULE')
                            value = {**suggested_schedule(target, branch), **merged.get(key, {}), **value}
                            validate_schedule(value)
                            value['repair'] = value['repair'] and target != 'files'
                            if not value['enabled'] or key in merged and merged[key] != value:
                                self._cancel(repo, target, branch)
                        merged[key] = value
                    if merged['interval_seconds'] not in INTERVALS or merged['mode'] not in ('FULL', 'INCREMENTAL'):
                        raise ValueError('INVALID_AUDIT_SCHEDULE')
                    merged['repair'] = merged['repair'] and target != 'files'
                    row.checkpoint = merged
                    if (merged.get('thorough_schedule', {}).get('enabled')
                            and not self.settings.reconciliation_scheduler_enabled):
                        # An old paused legacy sweep cannot block the explicit
                        # calendar sweep or wake up later as a duplicate.
                        repo.session.execute(update(Job).where(Job.stream_name == target,
                            Job.job_key.startswith(f'IDS:{target}:'), Job.status.in_(('PENDING', 'RETRY_WAIT')))
                            .values(status='CANCELLED', finished_at=now(), error_code='SUPERSEDED_BY_CALENDAR'))
                    changed_legacy_policy = previous_config and any(
                        key in values and previous_config.get(key) != merged[key] for key in ('mode', 'repair'))
                    if any(f'{branch}_schedule' in merged for branch in BRANCHES) or not merged['enabled'] or changed_legacy_policy:
                        self._cancel(repo, target, legacy=True)

    @staticmethod
    def _cancel(repo, stream, branch=None, legacy=False):
        query = update(Job).where(Job.stream_name == stream, Job.status.in_(('PENDING', 'RETRY_WAIT')))
        if legacy:
            query = query.where(or_(Job.job_key.startswith(f'configured:{stream}:'),
                Job.job_key.startswith(f'METADATA:{stream}:'), Job.job_key.startswith(f'LIGHT:{stream}:'),
                Job.job_key.startswith(f'DEEP:{stream}:')),
                ~Job.job_key.startswith(f'configured:{stream}:fast:'),
                ~Job.job_key.startswith(f'configured:{stream}:thorough:'))
        else:
            query = query.where(Job.job_key.startswith(f'configured:{stream}:{branch}:'))
        repo.session.execute(query.values(status='CANCELLED', finished_at=now(), error_code='CANCELLED_BY_ADMIN'))

    def tick(self):
        # One lock covers configured, calendar and fallback decisions across workers.
        with self.uow.processing_lock('reconciliation-scheduler') as acquired:
            return self._tick() if acquired else []

    def _tick(self):
        queue, stamp = AuditQueue(self.uow, self.settings), now()
        with self.uow() as repo:
            configured = {row.stream_name: dict(row.checkpoint or {}) for row in repo.session.scalars(
                select(Job).where(Job.status == 'CONFIGURED', Job.job_key.startswith('schedule:')))}
        decisions = []
        for stream, plan in configured.items():
            calendar = any(f'{branch}_schedule' in plan for branch in BRANCHES)
            if calendar:
                for branch in BRANCHES:
                    value = plan.get(f'{branch}_schedule')
                    if not value or not value['enabled']:
                        continue
                    slot, _ = schedule_slot(value, stamp)
                    if slot is None:
                        continue
                    mode = 'FULL' if branch == 'thorough' else 'INCREMENTAL'
                    kind = ('DEEP' if branch == 'thorough' else 'LIGHT') if stream == 'files' else 'METADATA'
                    kinds = [kind]
                    # The existing legacy ID sweep remains authoritative when enabled.
                    if branch == 'thorough' and stream != 'files' and not self.settings.reconciliation_scheduler_enabled:
                        kinds.append('IDS')
                    for kind in kinds:
                        key = f"configured:{stream}:{branch}:{kind}:{value['timezone']}:{value['cadence']}:{slot}"
                        decisions.append((kind, stream, key, mode, value['repair'] and kind == 'METADATA', True))
            elif plan['enabled']:
                kind = ('DEEP' if plan['mode'] == 'FULL' else 'LIGHT') if stream == 'files' else 'METADATA'
                slot = int(stamp.timestamp() // plan['interval_seconds'])
                decisions.append((kind, stream, f"configured:{stream}:{plan['interval_seconds']}:{slot}",
                                  plan['mode'], plan['repair'], False))
        if self.settings.reconciliation_scheduler_enabled:
            for kind, interval in [('METADATA', self.settings.reconciliation_light_interval_seconds),
                                   ('IDS', self.settings.reconciliation_id_sweep_interval_seconds),
                                   ('LIGHT', self.settings.reconciliation_light_interval_seconds), ('DEEP', 86400)]:
                for stream in ('files',) if kind in ('LIGHT', 'DEEP') else SCOPES[:-1]:
                    plan = configured.get(stream)
                    if plan and kind != 'IDS':
                        if any(f'{branch}_schedule' in plan for branch in BRANCHES):
                            continue
                        selected = ('DEEP' if plan['mode'] == 'FULL' else 'LIGHT') if stream == 'files' else 'METADATA'
                        if not plan['enabled'] or kind == selected:
                            continue
                    slot = int(stamp.timestamp() // interval)
                    decisions.append((kind, stream, f'{kind}:{stream}:{interval}:{slot}', None, False, False))
        return list(dict.fromkeys(self._enqueue(queue, stamp, *decision) for decision in decisions))

    def _enqueue(self, queue, stamp, kind, stream, key, mode, repair, calendar):
        with self.uow() as repo:
            active_query = select(Job.id).where(Job.stream_name == stream, Job.audit_type == kind,
                Job.status.in_(('PENDING', 'RUNNING', 'RETRY_WAIT')))
            if not self.settings.reconciliation_scheduler_enabled:
                active_query = active_query.where(or_(Job.status == 'RUNNING',
                    ~Job.job_key.contains(':'), Job.job_key.startswith('configured:')))
            if kind == 'METADATA' and mode is not None:
                active_query = active_query.where(or_(Job.checkpoint['mode'].as_string() == mode,
                                                      Job.checkpoint['mode'].as_string().is_(None)))
            active = repo.session.scalar(active_query.limit(1))
            if active:
                return active
            previous = repo.session.scalar(select(Job).where(or_(Job.job_key == key,
                Job.job_key.startswith(key + ':batch:'))).order_by(Job.created_at.desc(), Job.id.desc()).limit(1))
            resume = None
            if calendar and previous:
                cp = previous.checkpoint or {}
                if (previous.status == 'PARTIAL' and cp.get('coverage_status') == 'COVERAGE_BEHIND'
                        and cp.get('mode') == mode and bool(cp.get('repair_requested')) == repair
                        and cp.get('last_id') and cp.get('last_id') != cp.get('schedule_start_after')
                        and previous.finished_at and utc(previous.finished_at) + timedelta(
                            seconds=self.settings.reconciliation_retry_seconds) <= stamp):
                    resume = previous.id
                    key += f":batch:{cp.get('schedule_batch', 0) + 1}"
                else:
                    return previous.id
        return queue.enqueue(kind, stream, key, mode=mode, repair=repair,
                             resume_from=resume, scheduled_cycle=key.split(':batch:')[0] if calendar else None)
