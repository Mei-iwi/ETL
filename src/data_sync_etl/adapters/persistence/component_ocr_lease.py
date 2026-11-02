"""Component-owned page leases; never stored in the Master replica."""
import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlalchemy import Column, DateTime, Index, Integer, String, Table, and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError

from data_sync_etl.adapters.persistence.component_ocr_outbox import (
    component_metadata,
    component_tables,
    record_snapshot,
    require_isolated_prototype,
)
from data_sync_etl.domain.core import DomainError, new_id

component_page_leases = Table(
    'component_ocr_page_leases', component_metadata,
    Column('namespace', String(255), primary_key=True),
    Column('source_task_id', String(255), primary_key=True),
    Column('generation', Integer, primary_key=True),
    Column('worker_id', String(100)),
    Column('claim_token', String(30)),
    Column('lease_expires_at', DateTime(timezone=True)),
    Column('heartbeat_at', DateTime(timezone=True)),
    Column('next_retry_at', DateTime(timezone=True)),
    Column('attempts', Integer, nullable=False, server_default='0'),
    Column('last_error_code', String(80)),
)
Index('uq_component_ocr_version_generation', component_tables['job'].c.namespace,
      component_tables['job'].c.source_version_id, component_tables['job'].c.generation, unique=True)


def _clock():
    return datetime.now(timezone.utc)


class ComponentOCRLeaseQueue:
    """Claim and renew server-owned component pages with a fencing token.

    PostgreSQL is the intended backend. The caller must authorize the source
    before scheduling and again before content access and completion.
    """

    component_owned = True

    def __init__(self, engine, *, verify_mapping=None, lease_seconds=120, max_retries=3):
        if lease_seconds < 3 or max_retries < 1:
            raise ValueError('COMPONENT_INVALID_LEASE_CONFIG')
        require_isolated_prototype(engine)
        self.engine, self.lease_seconds, self.max_retries = engine, lease_seconds, max_retries
        self.heartbeat_timeout = lease_seconds
        self.verify_mapping = verify_mapping

    def schedule_authorized_source_job(self):
        # The caller must submit a fully resolved, authorized version explicitly.
        return False

    def submit(self, *, namespace, source_version_id, master_version_id, master_resource_id,
               generation, source_hash, processing_fingerprint, total_pages):
        if type(total_pages) is not int or not 1 <= total_pages <= 2000:
            raise DomainError('COMPONENT_INVALID_PAGE_COUNT')
        identity = [namespace, source_version_id, generation, source_hash, processing_fingerprint]
        job_id = sha256(json.dumps(identity, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
        job = dict(namespace=namespace, source_job_id=job_id, generation=generation,
                   source_version_id=source_version_id, master_version_id=master_version_id,
                   master_resource_id=master_resource_id, source_hash=source_hash,
                   processing_fingerprint=processing_fingerprint, status='PENDING', total_pages=total_pages,
                   completed_pages=0, failed_pages=0, revision=1, tombstone=False)
        if self.verify_mapping is None or not self.verify_mapping('job', job):
            raise DomainError('COMPONENT_CONTENT_UNAUTHORIZED')
        try:
            self._persist_submission(job)
        except IntegrityError:
            raise DomainError('COMPONENT_GENERATION_CONFLICT') from None
        return job_id

    def _persist_submission(self, job):
        namespace, generation = job['namespace'], job['generation']
        total_pages, job_id = job['total_pages'], job['source_job_id']
        master_version_id = job['master_version_id']
        with self.engine.begin() as conn:
            if conn.dialect.name not in ('postgresql', 'sqlite'):
                raise DomainError('COMPONENT_DATABASE_UNSUPPORTED')
            insert = pg_insert if conn.dialect.name == 'postgresql' else sqlite_insert
            record_snapshot(conn, 'job', job)
            for number in range(1, total_pages + 1):
                task_id = sha256(f'{job_id}:{number}'.encode()).hexdigest()
                snapshot = dict(namespace=namespace, source_task_id=task_id, generation=generation,
                                source_job_id=job_id, master_version_id=master_version_id,
                                page_num=number, status='PENDING', retry_count=0, error_code=None,
                                verified=False, revision=1, tombstone=False)
                record_snapshot(conn, 'page', snapshot)
                statement = insert(component_page_leases).values(namespace=namespace, source_task_id=task_id,
                                                                  generation=generation, attempts=0)
                conn.execute(statement.on_conflict_do_nothing(
                    index_elements=['namespace', 'source_task_id', 'generation']))

    def claim(self, worker_id):
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 100:
            raise DomainError('COMPONENT_INVALID_WORKER')
        pages, jobs, leases = component_tables['page'], component_tables['job'], component_page_leases
        stamp = _clock()
        link = and_(pages.c.namespace == leases.c.namespace, pages.c.source_task_id == leases.c.source_task_id,
                    pages.c.generation == leases.c.generation)
        job_link = and_(jobs.c.namespace == pages.c.namespace, jobs.c.source_job_id == pages.c.source_job_id,
                        jobs.c.generation == pages.c.generation)
        with self.engine.begin() as conn:
            row = conn.execute(select(pages, jobs.c.source_version_id, jobs.c.source_hash,
                                      jobs.c.processing_fingerprint).select_from(
                pages.join(leases, link).join(jobs, job_link)).where(
                    pages.c.status.in_(('PENDING', 'RUNNING')), pages.c.tombstone.is_(False),
                    jobs.c.tombstone.is_(False), jobs.c.status.in_(('PENDING', 'RUNNING')),
                    or_(leases.c.next_retry_at.is_(None), leases.c.next_retry_at <= stamp),
                    or_(leases.c.lease_expires_at.is_(None), leases.c.lease_expires_at <= stamp),
                ).order_by(pages.c.namespace, pages.c.source_job_id, pages.c.page_num)
                 .limit(1).with_for_update(skip_locked=True)).mappings().first()
            if row is None:
                return None
            key = {'namespace': row['namespace'], 'source_task_id': row['source_task_id'],
                   'generation': row['generation']}
            token = new_id()
            claimed = conn.execute(update(leases).where(
                *[leases.c[name] == value for name, value in key.items()],
                or_(leases.c.lease_expires_at.is_(None), leases.c.lease_expires_at <= stamp),
            ).values(worker_id=worker_id, claim_token=token, heartbeat_at=stamp,
                     lease_expires_at=stamp + timedelta(seconds=self.lease_seconds), next_retry_at=None))
            if claimed.rowcount != 1:
                return None
            page_fields = set(pages.c.keys())
            running = {name: row[name] for name in page_fields}
            running.update(status='RUNNING', revision=row['revision'] + 1, error_code=None)
            record_snapshot(conn, 'page', running)
            completed = dict(running, status='SUCCEEDED', verified=True, revision=running['revision'] + 1)
            return {'id': row['source_task_id'], 'job_id': row['source_job_id'], 'claim_token': token,
                    'component_snapshot': completed, 'source_version_id': row['source_version_id'],
                    'source_hash': row['source_hash'], 'processing_fingerprint': row['processing_fingerprint']}

    def owns(self, conn, snapshot, token):
        if not token:
            return False
        leases = component_page_leases
        return conn.execute(select(leases.c.claim_token).where(
            leases.c.namespace == snapshot['namespace'], leases.c.source_task_id == snapshot['source_task_id'],
            leases.c.generation == snapshot['generation'], leases.c.claim_token == token,
            leases.c.lease_expires_at > _clock(),
        ).with_for_update()).scalar_one_or_none() is not None

    def heartbeat(self, task):
        snapshot, token = task['component_snapshot'], task['claim_token']
        stamp, leases = _clock(), component_page_leases
        with self.engine.begin() as conn:
            result = conn.execute(update(leases).where(
                leases.c.namespace == snapshot['namespace'], leases.c.source_task_id == snapshot['source_task_id'],
                leases.c.generation == snapshot['generation'], leases.c.claim_token == token,
                leases.c.lease_expires_at > stamp,
            ).values(heartbeat_at=stamp, lease_expires_at=stamp + timedelta(seconds=self.lease_seconds)))
            return result.rowcount == 1

    def after_complete(self, conn, snapshot, token):
        if not self.owns(conn, snapshot, token):
            raise DomainError('COMPONENT_OCR_LEASE_LOST')
        leases = component_page_leases
        conn.execute(update(leases).where(
            leases.c.namespace == snapshot['namespace'], leases.c.source_task_id == snapshot['source_task_id'],
            leases.c.generation == snapshot['generation'], leases.c.claim_token == token,
        ).values(claim_token=None, worker_id=None, lease_expires_at=None, heartbeat_at=None,
                 next_retry_at=None, last_error_code=None))
        self._refresh_job(conn, snapshot)

    def _refresh_job(self, conn, snapshot):
        pages, jobs = component_tables['page'], component_tables['job']
        job = conn.execute(select(jobs).where(
            jobs.c.namespace == snapshot['namespace'], jobs.c.source_job_id == snapshot['source_job_id'],
            jobs.c.generation == snapshot['generation'],
        ).with_for_update()).mappings().one()
        rows = conn.execute(select(pages).where(
            pages.c.namespace == snapshot['namespace'], pages.c.source_job_id == snapshot['source_job_id'],
            pages.c.generation == snapshot['generation'], pages.c.tombstone.is_(False),
        )).mappings().all()
        completed = sum(row['status'] == 'SUCCEEDED' and row['verified'] for row in rows)
        failed = sum(row['status'] == 'FAILED' for row in rows)
        status = ('COMPLETED' if failed == 0 else 'COMPLETED_WITH_ERRORS') \
            if len(rows) == job['total_pages'] and completed + failed == job['total_pages'] else 'RUNNING'
        if (job['status'], job['completed_pages'], job['failed_pages']) != (status, completed, failed):
            record_snapshot(conn, 'job', dict(job, status=status, completed_pages=completed,
                                              failed_pages=failed, revision=job['revision'] + 1))

    def fail(self, task, error=None):
        snapshot, token = task['component_snapshot'], task['claim_token']
        leases, pages = component_page_leases, component_tables['page']
        with self.engine.begin() as conn:
            if not self.owns(conn, snapshot, token):
                return False
            key = (leases.c.namespace == snapshot['namespace'], leases.c.source_task_id == snapshot['source_task_id'],
                   leases.c.generation == snapshot['generation'])
            lease = conn.execute(select(leases).where(*key).with_for_update()).mappings().one()
            attempts = lease['attempts'] + 1
            terminal = attempts >= self.max_retries
            current = conn.execute(select(pages).where(
                pages.c.namespace == snapshot['namespace'], pages.c.source_task_id == snapshot['source_task_id'],
                pages.c.generation == snapshot['generation'],
            ).with_for_update()).mappings().one()
            record_snapshot(conn, 'page', dict(current, status='FAILED' if terminal else 'PENDING',
                                               retry_count=attempts, error_code='COMPONENT_OCR_FAILED',
                                               revision=current['revision'] + 1))
            conn.execute(update(leases).where(*key, leases.c.claim_token == token).values(
                attempts=attempts, last_error_code='COMPONENT_OCR_FAILED', claim_token=None, worker_id=None,
                lease_expires_at=None, heartbeat_at=None,
                next_retry_at=None if terminal else _clock() + timedelta(seconds=min(300, 2 ** attempts))))
            self._refresh_job(conn, snapshot)
            return True

    def recover_stale(self):
        # Expired leases are reclaimed atomically in claim(); no background reset.
        return {'recovered': 0}
