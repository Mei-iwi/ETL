"""Leases on the existing SOURCE tables; no component schema or secondary queue."""
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from data_sync_etl.domain.core import DomainError, now

JOB_FIELDS = frozenset({'id', 'resource_version_id', 'status', 'total_pages', 'completed_pages',
                        'failed_pages', 'started_at', 'completed_at', 'updated_at'})
TASK_FIELDS = frozenset({'id', 'job_id', 'page_num', 'local_image_path', 'status', 'worker_id',
                         'retry_count', 'error_message', 'heartbeat_at', 'started_at',
                         'completed_at', 'created_at', 'updated_at'})


class PostgreSQLSourceOCRQueue:
    def __init__(self, writer, *, heartbeat_timeout, max_retries, max_active=1):
        self.writer, self.engine = writer, writer.engine
        self.heartbeat_timeout, self.max_retries = heartbeat_timeout, max_retries
        self.max_active = max_active

    def _guard(self, conn, identity):
        if not self.writer.authorize_identity(dict(identity)):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        contract = self.writer.contract_for(identity)
        conn.execute(text("SELECT set_config('statement_timeout', :ms, true)"),
                     {'ms': str(self.writer.contract.statement_timeout_seconds * 1000)})
        version = conn.execute(text('SELECT source_hash, lifecycle_status FROM public.resource_versions '
                                    'WHERE id=:id FOR UPDATE'),
                               {'id': identity['source_version_id']}).mappings().one_or_none()
        if not version or version['source_hash'].lower() != identity['source_hash']:
            raise DomainError('SOURCE_VERSION_MUTATED')
        actor = conn.execute(text('SELECT is_active FROM public.users WHERE id=:id'),
                             {'id': identity['source_user_id']}).scalar()
        if actor is not True:
            raise DomainError('SOURCE_OCR_ACTOR_INVALID')
        for table, fields in [('ocr_jobs', JOB_FIELDS), ('ocr_page_tasks', TASK_FIELDS)]:
            for action in ('select', 'update'):
                if not contract.allows(
                        namespace=identity['namespace'], table=table, action=action, fields=fields,
                        actor_id=identity['actor_id'], role=identity['role'],
                        source_version_id=identity['source_version_id'], lifecycle=version['lifecycle_status']):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')

    def owns(self, current, claimed):
        return bool(current and current['status'] == 'PROCESSING'
                    and current['worker_id'] == claimed['worker_id']
                    and current['retry_count'] == claimed['retry_count']
                    and current['started_at'] == claimed['started_at']
                    and (current['heartbeat_at'] or current['started_at']) > now() - timedelta(seconds=self.heartbeat_timeout))

    def _job(self, conn, identity, job_id):
        job = conn.execute(text('SELECT * FROM public.ocr_jobs WHERE id=:id FOR UPDATE'),
                           {'id': job_id}).mappings().one_or_none()
        if not job or job['resource_version_id'] != identity['source_version_id']:
            raise DomainError('SOURCE_OCR_JOB_INVALID')
        return job

    def _counts(self, conn, job_id):
        rows = conn.execute(text('SELECT page_num, status FROM public.ocr_page_tasks WHERE job_id=:id'),
                            {'id': job_id}).mappings().all()
        job = conn.execute(text('SELECT total_pages FROM public.ocr_jobs WHERE id=:id'),
                           {'id': job_id}).mappings().one()
        total = job['total_pages']
        numbers = [row['page_num'] for row in rows]
        if len(set(numbers)) != len(numbers) or any(not 1 <= number <= total for number in numbers):
            raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
        completed = sum(row['status'] == 'COMPLETED' for row in rows)
        failed = sum(row['status'] == 'FAILED' for row in rows)
        terminal = total > 0 and completed + failed == total
        active = any(row['status'] == 'PROCESSING' for row in rows)
        status = (('FAILED' if failed else 'COMPLETED') if terminal
                  else ('PROCESSING' if active or completed or failed else 'QUEUED'))
        conn.execute(text('UPDATE public.ocr_jobs SET completed_pages=:ok, failed_pages=:failed, '
                          'status=:status, completed_at=:finished, updated_at=:stamp WHERE id=:id'),
                     {'ok': completed, 'failed': failed, 'status': status,
                      'finished': now() if terminal else None, 'stamp': now(), 'id': job_id})

    def claim(self, identity, job_id, worker_id, *, allow_claim=None):
        if not isinstance(worker_id, str) or not 1 <= len(worker_id) <= 100:
            raise DomainError('SOURCE_OCR_WORKER_ID_INVALID')
        with self.engine.begin() as conn:
            self._guard(conn, identity)
            job = self._job(conn, identity, job_id)
            if job['status'] not in ('QUEUED', 'PROCESSING'):
                return None
            if allow_claim is not None and not allow_claim():
                return None
            from hashlib import sha256
            key = int.from_bytes(sha256(('source-ocr-admission:' + identity['namespace']).encode()).digest()[:8],
                                 'big', signed=True)
            if not conn.execute(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': key}).scalar():
                return None
            running = conn.execute(text('SELECT count(*) FROM public.ocr_page_tasks p '
                'JOIN public.ocr_jobs j ON j.id=p.job_id WHERE (j.resource_version_id IN :versions '
                'OR j.triggered_by=:actor) '
                "AND p.status='PROCESSING'").bindparams(bindparam('versions', expanding=True)),
                {'versions': sorted(self.writer.contract.version_ids), 'actor': identity['source_user_id']}).scalar_one()
            if running >= self.max_active:
                return None
            task = conn.execute(text("SELECT * FROM public.ocr_page_tasks WHERE job_id=:job "
                                     "AND status='PENDING' ORDER BY page_num, id LIMIT 1 FOR UPDATE SKIP LOCKED"),
                                {'job': job_id}).mappings().one_or_none()
            if task is None:
                return None
            stamp, token = now(), uuid4().hex
            # worker_id is the available durable fencing slot in SOURCE's schema.
            owner = f'{worker_id[:60]}:{token}'
            conn.execute(text("UPDATE public.ocr_page_tasks SET status='PROCESSING', worker_id=:owner, "
                              'started_at=:stamp, heartbeat_at=:stamp, updated_at=:stamp WHERE id=:id'),
                         {'id': task['id'], 'owner': owner, 'stamp': stamp})
            conn.execute(text("UPDATE public.ocr_jobs SET status='PROCESSING', "
                              'started_at=COALESCE(started_at,:stamp), updated_at=:stamp WHERE id=:id'),
                         {'id': job_id, 'stamp': stamp})
            return {**dict(task), 'status': 'PROCESSING', 'worker_id': owner,
                    'started_at': stamp, 'heartbeat_at': stamp, 'source_job_id': job_id}

    def transition(self, identity, claimed, *, complete=False, error=None, heartbeat=False, before_complete=None):
        with self.engine.begin() as conn:
            self._guard(conn, identity)
            self._job(conn, identity, claimed['source_job_id'])
            current = conn.execute(text('SELECT * FROM public.ocr_page_tasks WHERE id=:id FOR UPDATE'),
                                   {'id': claimed['id']}).mappings().one_or_none()
            if not self.owns(current, claimed):
                raise DomainError('SOURCE_OCR_LEASE_LOST')
            if heartbeat:
                conn.execute(text('UPDATE public.ocr_page_tasks SET heartbeat_at=:stamp, updated_at=:stamp WHERE id=:id'),
                             {'stamp': now(), 'id': claimed['id']})
                return True
            if complete:
                if before_complete is None:
                    raise DomainError('SOURCE_OCR_VERIFIED_COMPLETION_REQUIRED')
                before_complete()
            retries = current['retry_count'] + (0 if complete else 1)
            status = 'COMPLETED' if complete else ('FAILED' if retries >= self.max_retries else 'PENDING')
            conn.execute(text('UPDATE public.ocr_page_tasks SET status=:status, retry_count=:retries, '
                              'error_message=:error, completed_at=:finished, heartbeat_at=:stamp, '
                              'updated_at=:stamp WHERE id=:id'),
                         {'status': status, 'retries': retries, 'error': None if complete else error,
                          'finished': now() if status in ('COMPLETED', 'FAILED') else None,
                          'stamp': now(), 'id': claimed['id']})
            self._counts(conn, claimed['source_job_id'])
            if not self.writer.authorize_identity(dict(identity)):
                raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        return True

    def recover(self, identity, job_id):
        with self.engine.begin() as conn:
            self._guard(conn, identity)
            if self._job(conn, identity, job_id)['status'] == 'CANCELLED':
                return 0
            rows = conn.execute(text("SELECT * FROM public.ocr_page_tasks WHERE job_id=:job "
                                     "AND status='PROCESSING' AND COALESCE(heartbeat_at,started_at,updated_at)<:cutoff "
                                     'FOR UPDATE SKIP LOCKED'),
                                {'job': job_id, 'cutoff': now() - timedelta(seconds=self.heartbeat_timeout)}).mappings().all()
            for task in rows:
                retries = task['retry_count'] + 1
                conn.execute(text('UPDATE public.ocr_page_tasks SET status=:status, retry_count=:retries, '
                                  "worker_id=NULL, error_message='SOURCE_OCR_LEASE_EXPIRED', updated_at=:stamp WHERE id=:id"),
                             {'status': 'FAILED' if retries >= self.max_retries else 'PENDING',
                              'retries': retries, 'stamp': now(), 'id': task['id']})
            if rows:
                self._counts(conn, job_id)
            return len(rows)

    def retry(self, identity, job_id):
        with self.engine.begin() as conn:
            self._guard(conn, identity)
            job = self._job(conn, identity, job_id)
            if job['status'] not in ('FAILED', 'COMPLETED'):
                raise DomainError('SOURCE_OCR_RETRY_CONFLICT')
            conn.execute(text("UPDATE public.ocr_page_tasks SET status='PENDING', worker_id=NULL, "
                              'retry_count=0, error_message=NULL, completed_at=NULL, updated_at=:stamp '
                              "WHERE job_id=:id AND status='FAILED'"), {'id': job_id, 'stamp': now()})
            self._counts(conn, job_id)

    def prepare_missing(self, identity, job_id, pages):
        if not pages:
            return
        with self.engine.begin() as conn:
            self._guard(conn, identity)
            job = self._job(conn, identity, job_id)
            if any(type(page) is not int or not 1 <= page <= job['total_pages'] for page in pages):
                raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
            rows = conn.execute(text('SELECT id,status,page_num FROM public.ocr_page_tasks WHERE job_id=:id FOR UPDATE'),
                                {'id': job_id}).mappings().all()
            for row in rows:
                if row['page_num'] in pages:
                    if row['status'] == 'PROCESSING':
                        raise DomainError('SOURCE_OCR_ALREADY_RUNNING')
                    conn.execute(text("UPDATE public.ocr_page_tasks SET status='PENDING', worker_id=NULL, "
                                      'completed_at=NULL, updated_at=:stamp WHERE id=:id'),
                                 {'id': row['id'], 'stamp': now()})
            self._counts(conn, job_id)


def source_sql_error(exc):
    """Preserve SQLSTATE diagnostics without propagating SQL, paths or DSNs."""
    if isinstance(exc, SQLAlchemyError):
        code = getattr(getattr(exc, 'orig', None), 'sqlstate', None)
        return DomainError(f'SOURCE_OCR_SQL_{code}' if code and code.isalnum() else 'SOURCE_OCR_DATABASE_UNAVAILABLE')
    return exc
