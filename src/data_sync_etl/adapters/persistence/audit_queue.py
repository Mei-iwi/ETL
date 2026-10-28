"""Durable audit-only queue. Every write is fenced by token AND unexpired lease."""
from datetime import UTC, timedelta

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from data_sync_etl.adapters.persistence.queue_admission import admission_lock, processing_available
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.db.models import SyncReconciliationRun
from data_sync_etl.domain.core import DomainError, new_id, now


def utc(stamp):
    return stamp.replace(tzinfo=UTC) if stamp and stamp.tzinfo is None else stamp


class AuditQueue:
    def __init__(self, uow, settings):
        self.uow, self.settings = uow, settings

    def enqueue(self, audit_type='DEEP', stream='files', job_key=None, *, mode=None, repair=False,
                resume_from=None, scheduled_cycle=None):
        if audit_type not in ('DEEP', 'LIGHT', 'METADATA', 'IDS') or stream not in ('files', 'resources', 'subjects', 'grades', 'users'):
            raise DomainError('INVALID_AUDIT_SCOPE')
        if (audit_type in ('DEEP', 'LIGHT')) != (stream == 'files'):
            raise DomainError('INVALID_AUDIT_SCOPE')
        key = job_key or new_id()
        try:
            with self.uow() as repo:
                return self._enqueue_in(repo, audit_type, stream, key, mode, repair, resume_from, scheduled_cycle)
        except IntegrityError:
            with self.uow() as repo:
                return repo.session.scalar(select(Job.id).where(Job.job_key == key))

    def _enqueue_in(self, repo, audit_type, stream, key, mode, repair, resume_from=None, scheduled_cycle=None):
        existing = repo.session.scalar(select(Job).where(Job.job_key == key))
        if existing:
            return existing.id
        previous = repo.session.scalar(select(Job).where(Job.audit_type == audit_type,
            Job.stream_name == stream).order_by(Job.created_at.desc()).limit(1))
        manual_full = audit_type == 'DEEP' and stream == 'files' and mode == 'FULL' and scheduled_cycle is None and ':' not in key
        if manual_full:
            previous = None  # A new explicit full scan starts at the beginning.
        if scheduled_cycle is not None:
            # Calendar cycles must not inherit cursors from manual work or the
            # other FAST/THOROUGH branch, even when both use METADATA.
            previous = repo.session.get(Job, resume_from) if resume_from else None
            if resume_from and (previous is None or previous.status != 'PARTIAL'
                    or previous.audit_type != audit_type or previous.stream_name != stream
                    or (previous.checkpoint or {}).get('schedule_cycle') != scheduled_cycle):
                raise DomainError('INVALID_AUDIT_SCOPE')
        # File scans continue by keyset across independent budgeted jobs.
        checkpoint = {}
        if previous and previous.checkpoint and previous.status != 'FAILED':
            checkpoint = {k: v for k, v in previous.checkpoint.items() if k in (
                'last_id', 'phase', 'generation', 'source_seen', 'source_summary')}
            if audit_type == 'METADATA' and previous.status == 'PARTIAL' and previous.checkpoint.get('last_id'):
                checkpoint = {k: v for k, v in previous.checkpoint.items() if k in (
                    'last_id', 'scan_started', 'since', 'watermark_next', 'generation',
                    'auth_scope_complete', 'auth_identity_digest', 'auth_identity_count')}
            elif audit_type == 'METADATA':
                checkpoint = {}
        if mode == 'FULL' and not (previous and previous.status == 'PARTIAL'
                                   and (previous.checkpoint or {}).get('mode') == 'FULL'):
            checkpoint = {}
        if previous and bool((previous.checkpoint or {}).get('repair_requested')) != repair:
            checkpoint = {}
        if mode is not None:
            checkpoint.update(mode=mode, repair_requested=repair)
        if manual_full:
            checkpoint.update(manual_full_scan=True, manual_batch_start=None)
        if scheduled_cycle is not None:
            cp = (previous.checkpoint or {}) if previous else {}
            if previous and audit_type in ('METADATA', 'IDS'):
                checkpoint.update({key: value for key, value in cp.items() if key in (
                    'records_scanned', 'matched_count', 'mismatched_count', 'not_verified_count',
                    'source_pages_read', 'repaired_count', 'auth_scope_complete',
                    'auth_identity_count', 'auth_identity_digest')})
            if previous and audit_type in ('LIGHT', 'DEEP'):
                for metric in ('records_scanned', 'bytes_read', 'mismatched_count', 'not_verified_count'):
                    checkpoint[f'cycle_{metric}'] = cp.get(f'cycle_{metric}', 0) + cp.get(metric, 0)
            checkpoint.update(schedule_cycle=scheduled_cycle,
                schedule_batch=cp.get('schedule_batch', -1) + 1,
                schedule_start_after=checkpoint.get('last_id'))
        row = Job(id=new_id(), job_key=key, audit_type=audit_type, stream_name=stream,
                  status='PENDING', checkpoint=checkpoint)
        repo.session.add(row)
        repo.session.flush()
        return row.id

    def resume_full_files(self):
        """Continue explicitly requested full scans, using the same durable job.

        Only a successfully checkpointed, advancing budget boundary is resumed.
        Errors keep the existing bounded retry policy; completed/unverified
        cycles and batches without progress are never restarted here.
        """
        stamp, resumed = now(), []
        with self.uow() as repo:
            admission_lock(repo.session, 'audit')
            rows = repo.session.scalars(select(Job).where(Job.status == 'PARTIAL', Job.audit_type == 'DEEP',
                Job.stream_name == 'files', Job.checkpoint['manual_full_scan'].as_boolean().is_(True),
                Job.checkpoint['coverage_status'].as_string() == 'COVERAGE_BEHIND',
                Job.checkpoint['cycle_finished'].as_boolean().is_not(True),
                Job.checkpoint['last_id'].as_string().is_not(None),
                or_(Job.checkpoint['manual_batch_start'].as_string().is_(None),
                    Job.checkpoint['last_id'].as_string() != Job.checkpoint['manual_batch_start'].as_string()),
                Job.finished_at <= stamp - timedelta(seconds=self.settings.reconciliation_retry_seconds))
                .order_by(Job.created_at, Job.id).with_for_update(skip_locked=True).limit(20))
            for row in rows:
                cp = dict(row.checkpoint or {})
                if (cp.get('coverage_status') != 'COVERAGE_BEHIND' or cp.get('cycle_finished')
                        or not cp.get('last_id') or cp['last_id'] == cp.get('manual_batch_start')):
                    continue
                for metric in ('records_scanned', 'matched_count', 'mismatched_count', 'not_verified_count',
                               'bytes_read', 'sha256_computed_count', 'observed_file_bytes', 'observed_file_count', 'execution_time_ms'):
                    cp[f'cycle_{metric}'] = cp.get(f'cycle_{metric}', 0) + cp.get(metric, 0)
                    cp[metric] = 0
                cp['manual_batch_start'] = cp['last_id']
                row.checkpoint, row.status, row.finished_at = cp, 'PENDING', None
                row.attempts, row.next_retry_at, row.error_code = 0, None, None
                resumed.append(row.id)
        return resumed

    def enqueue_all(self, *, mode='INCREMENTAL', repair=False):
        if mode not in ('INCREMENTAL', 'FULL'):
            raise DomainError('INVALID_AUDIT_SCOPE')
        jobs = []
        with self.uow() as repo:
            for stream in ('grades', 'subjects', 'users', 'resources', 'files'):
                kind = ('DEEP' if mode == 'FULL' else 'LIGHT') if stream == 'files' else 'METADATA'
                job_id = self._enqueue_in(repo, kind, stream, new_id(), mode, repair and stream != 'files')
                jobs.append({'job_id': job_id, 'stream': stream, 'audit_type': kind})
        return jobs

    def claim(self, job_id=None):
        with self.uow() as repo:
            admission_lock(repo.session, 'audit')
            stamp = now()
            active = and_(Job.status == 'RUNNING', Job.lease_expires_at > stamp)
            if repo.session.scalar(select(func.count()).select_from(Job).where(active)) >= self.settings.reconciliation_max_active:
                return None
            other = aliased(Job)
            busy_stream = select(other.id).where(other.stream_name == Job.stream_name,
                other.id != Job.id, other.status == 'RUNNING', other.lease_expires_at > stamp).exists()
            eligible = or_(and_(Job.status.in_(('PENDING', 'RETRY_WAIT')),
                                or_(Job.next_retry_at.is_(None), Job.next_retry_at <= stamp)),
                           and_(Job.status == 'RUNNING', Job.lease_expires_at <= stamp))
            if job_id is not None:
                eligible = and_(eligible, Job.id == job_id)
            if not self.settings.reconciliation_scheduler_enabled:
                # Scheduled slot keys contain ':'; manual API keys are UUID/ULID.
                # Disabling the scheduler also pauses unclaimed scheduled work.
                eligible = and_(eligible, or_(~Job.job_key.contains(':'), Job.job_key.startswith('configured:')))
            excluded = []
            while True:
                row = repo.session.scalar(select(Job).where(eligible, ~busy_stream,
                    Job.stream_name.not_in(excluded)).order_by(
                    case((~Job.job_key.contains(':'), 0), else_=1), Job.created_at, Job.id)
                    .with_for_update(skip_locked=True).limit(1))
                if row is None:
                    return None
                if not processing_available(repo.session, f'reconciliation-{row.stream_name}'):
                    excluded.append(row.stream_name)
                    continue  # A live execution lock must not spend another retry.
                if row.attempts >= self.settings.reconciliation_retry_max_attempts:
                    row.status, row.error_code, row.finished_at = 'FAILED', 'AUDIT_RETRY_EXHAUSTED', stamp
                    repo.session.flush()
                    continue
                break
            token = new_id()
            attempts = row.attempts + 1
            changed = repo.session.execute(update(Job).where(Job.id == row.id, eligible).values(
                status='RUNNING', claim_token=token, heartbeat_at=stamp,
                lease_expires_at=stamp + timedelta(seconds=self.settings.reconciliation_lease_seconds),
                attempts=attempts).execution_options(synchronize_session=False))
            if changed.rowcount != 1:
                return None
            return {'id': row.id, 'claim_token': token, 'audit_type': row.audit_type,
                    'stream_name': row.stream_name, 'checkpoint': dict(row.checkpoint or {}),
                    'created_at': utc(row.created_at), 'attempts': attempts,
                    'queue_wait_seconds': max(0, (stamp - utc(row.created_at)).total_seconds())}

    def owned(self, session, claim):
        row = session.scalar(select(Job).where(Job.id == claim['id']).with_for_update())
        if not row or row.status != 'RUNNING' or row.claim_token != claim['claim_token'] or utc(row.lease_expires_at) <= now():
            raise DomainError('AUDIT_LEASE_LOST')
        return row

    def heartbeat(self, claim):
        with self.uow() as repo:
            row = self.owned(repo.session, claim)
            row.heartbeat_at = now()
            row.lease_expires_at = now() + timedelta(seconds=self.settings.reconciliation_lease_seconds)

    def fail(self, claim):
        try:
            with self.uow() as repo:
                row = self.owned(repo.session, claim)
                exhausted = row.attempts >= self.settings.reconciliation_retry_max_attempts
                row.status = 'FAILED' if exhausted else 'RETRY_WAIT'
                row.error_code = 'AUDIT_RETRY_EXHAUSTED' if exhausted else 'AUDIT_UNAVAILABLE'
                row.next_retry_at = now() + timedelta(seconds=min(3600,
                    self.settings.reconciliation_retry_seconds * 2 ** (row.attempts - 1)))
                row.claim_token, row.lease_expires_at = None, None
                run = repo.session.get(SyncReconciliationRun, claim['id'])
                if run:
                    run.status, run.finished_at = 'FAILED' if exhausted else 'PARTIAL', now()
                    run.summary_meta = {**(run.summary_meta or {}), 'error_code': row.error_code}
        except DomainError:
            pass  # An obsolete worker cannot mark a new owner's work failed.
