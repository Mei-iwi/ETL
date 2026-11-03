"""SOURCE OCR job creation using the existing SOURCE transaction and ID counter.

The caller supplies an owner-approved OCR contract and a server-owned identity
authorizer. This adapter is not a replacement for the OCR worker or the SOURCE
to MASTER projection; a caller must not advertise a completed pipeline merely
because a job was created.
"""
import re
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from data_sync_etl.adapters.source.id_counter import PostgreSQLSourceIDCounter
from data_sync_etl.application.source_ids import SourceIDService
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ocr_contract import SourceOCRContract

_JOB_COLUMNS = frozenset({'id', 'resource_version_id', 'triggered_by', 'status', 'total_pages',
                          'completed_pages', 'failed_pages', 'created_at', 'updated_at'})
_HASH = re.compile(r'^[0-9a-f]{64}$')
_TASK_COLUMNS = frozenset({'id', 'job_id', 'page_num', 'local_image_path', 'status', 'retry_count',
                           'created_at', 'updated_at'})


class PostgreSQLSourceOCRWriter:
    def __init__(self, engine, contract, *, authorize_identity, authorize_created_version=None,
                 authorize_namespace_version=None):
        if not isinstance(contract, SourceOCRContract) or not callable(authorize_identity):
            raise DomainError('SOURCE_OCR_CONTRACT_REQUIRED')
        if engine.dialect.name != 'postgresql':
            raise DomainError('SOURCE_OCR_DATABASE_INVALID')
        self.engine, self.contract, self.authorize_identity = engine, contract, authorize_identity
        self.authorize_created_version = authorize_created_version
        self.authorize_namespace_version = authorize_namespace_version

    def contract_for(self, identity):
        """Extend only this operation after independently revalidated authority."""
        version = identity['source_version_id']
        if (version not in self.contract.version_ids and self.contract.allow_etl_created_versions
                and callable(self.authorize_created_version) and self.authorize_created_version(dict(identity))):
            return replace(self.contract, version_ids=self.contract.version_ids | {version})
        if (version not in self.contract.version_ids and self.contract.namespace_scope is not None
                and callable(self.authorize_namespace_version) and self.authorize_namespace_version(dict(identity))):
            return replace(self.contract, version_ids=self.contract.version_ids | {version})
        return self.contract

    @staticmethod
    def next_id(connection, prefix):
        if prefix not in ('OCR', 'OPT', 'CUN'):
            raise DomainError('SOURCE_OCR_ID_PREFIX_INVALID')
        return SourceIDService(PostgreSQLSourceIDCounter(connection)).next_id(prefix, created_at=datetime.now(UTC))

    def ensure_job(self, *, namespace, master_version_id, source_version_id, source_hash,
                   actor_id, role, source_user_id):
        """Create at most one SOURCE job for a version, or return its existing ID.

        All IDs and role are server-derived; authorize_identity must attest the
        MASTER/SOURCE mapping and authenticated actor, including source_user_id.
        A version row lock serializes concurrent callers before the uniqueness
        check and counter allocation. This operation does not create page tasks.
        """
        identity = dict(namespace=namespace, master_version_id=master_version_id,
                        source_version_id=source_version_id, source_hash=source_hash,
                        actor_id=actor_id, role=role, source_user_id=source_user_id)
        if (not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash)
                or any(not isinstance(identity[key], str) or not 1 <= len(identity[key]) <= 30
                       for key in ('master_version_id', 'source_version_id', 'actor_id', 'source_user_id'))
                or not self.authorize_identity(dict(identity))):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        try:
            with self.engine.begin() as conn:
                conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                             {'timeout': str(self.contract.statement_timeout_seconds * 1000)})
                version = conn.execute(text(
                    'SELECT id, source_hash, lifecycle_status FROM public.resource_versions '
                    'WHERE id=:id FOR UPDATE'), {'id': source_version_id}).mappings().one_or_none()
                if not version or not isinstance(version['source_hash'], str) or version['source_hash'].lower() != source_hash:
                    raise DomainError('SOURCE_VERSION_MUTATED')
                lifecycle = version['lifecycle_status']
                permission = dict(namespace=namespace, table='ocr_jobs', role=role, actor_id=actor_id,
                                  source_version_id=source_version_id, lifecycle=lifecycle)
                if not self.contract_for(identity).allows(**permission, action='select', fields=_JOB_COLUMNS):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                actor = conn.execute(text('SELECT id, is_active FROM public.users WHERE id=:id'),
                                     {'id': source_user_id}).mappings().one_or_none()
                if not actor or actor['is_active'] is not True:
                    raise DomainError('SOURCE_OCR_ACTOR_INVALID')
                existing = conn.execute(text(
                    'SELECT id FROM public.ocr_jobs WHERE resource_version_id=:id LIMIT 2'),
                    {'id': source_version_id}).scalars().all()
                if len(existing) > 1:
                    raise DomainError('SOURCE_OCR_JOB_CONFLICT')
                if not self.authorize_identity(dict(identity)):
                    raise DomainError('SOURCE_OCR_UNAUTHORIZED')
                if existing:
                    return existing[0]
                if not self.contract_for(identity).allows(**permission, action='insert', fields=_JOB_COLUMNS):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                timestamp = datetime.now(UTC)
                job_id = SourceIDService(PostgreSQLSourceIDCounter(conn)).next_id('OCR', created_at=timestamp)
                conn.execute(text(
                    'INSERT INTO public.ocr_jobs (id, resource_version_id, triggered_by, status, total_pages, '
                    'completed_pages, failed_pages, created_at, updated_at) VALUES '
                    "(:id, :version, :actor, 'QUEUED', 0, 0, 0, :created, :created)"),
                    {'id': job_id, 'version': source_version_id, 'actor': source_user_id, 'created': timestamp})
                if not self.authorize_identity(dict(identity)):
                    raise DomainError('SOURCE_OCR_UNAUTHORIZED')
                return job_id
        except SQLAlchemyError as exc:
            from data_sync_etl.adapters.source.postgresql_ocr_queue import source_sql_error
            raise source_sql_error(exc) from None

    def ensure_page_task(self, *, namespace, master_version_id, source_version_id, source_hash,
                         actor_id, role, source_user_id, source_job_id, page_num, staging_path,
                         staging_root):
        """Register one real staged page, serializing duplicate checks on its SOURCE job.

        The staged image must already exist under the worker-owned root. This
        method does not create paths, recognize text, or mark pages completed.
        """
        identity = dict(namespace=namespace, master_version_id=master_version_id,
                        source_version_id=source_version_id, source_hash=source_hash,
                        actor_id=actor_id, role=role, source_user_id=source_user_id)
        if (not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash)
                or any(not isinstance(identity[key], str) or not 1 <= len(identity[key]) <= 30
                       for key in ('master_version_id', 'source_version_id', 'actor_id', 'source_user_id'))
                or not isinstance(source_job_id, str) or not 1 <= len(source_job_id) <= 30
                or type(page_num) is not int or not 1 <= page_num <= 2000
                or not self.authorize_identity(dict(identity))):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        try:
            root = Path(staging_root).resolve(strict=True)
            path = Path(staging_path).resolve(strict=True)
            if not root.is_dir() or not path.is_file() or not path.is_relative_to(root) or len(str(path)) > 500:
                raise DomainError('SOURCE_OCR_STAGING_INVALID')
        except (OSError, TypeError, ValueError):
            raise DomainError('SOURCE_OCR_STAGING_INVALID') from None
        try:
            with self.engine.begin() as conn:
                conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                             {'timeout': str(self.contract.statement_timeout_seconds * 1000)})
                version = conn.execute(text(
                    'SELECT id, source_hash, lifecycle_status FROM public.resource_versions '
                    'WHERE id=:id FOR UPDATE'), {'id': source_version_id}).mappings().one_or_none()
                if not version or not isinstance(version['source_hash'], str) or version['source_hash'].lower() != source_hash:
                    raise DomainError('SOURCE_VERSION_MUTATED')
                permission = dict(namespace=namespace, table='ocr_page_tasks', role=role, actor_id=actor_id,
                                  source_version_id=source_version_id, lifecycle=version['lifecycle_status'])
                if not self.contract_for(identity).allows(**permission, action='select', fields=_TASK_COLUMNS):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                job_permission = {**permission, 'table': 'ocr_jobs'}
                if not self.contract_for(identity).allows(**job_permission, action='select',
                                            fields={'id', 'resource_version_id', 'status', 'total_pages'}):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                actor = conn.execute(text('SELECT id, is_active FROM public.users WHERE id=:id'),
                                     {'id': source_user_id}).mappings().one_or_none()
                if not actor or actor['is_active'] is not True:
                    raise DomainError('SOURCE_OCR_ACTOR_INVALID')
                job = conn.execute(text(
                    'SELECT id, resource_version_id, status, total_pages FROM public.ocr_jobs '
                    'WHERE id=:id FOR UPDATE'), {'id': source_job_id}).mappings().one_or_none()
                if (not job or job['resource_version_id'] != source_version_id
                        or job['status'] not in ('QUEUED', 'PROCESSING')
                        or type(job['total_pages']) is not int or not page_num <= job['total_pages'] <= 2000):
                    raise DomainError('SOURCE_OCR_JOB_INVALID')
                rows = conn.execute(text(
                    'SELECT id, local_image_path FROM public.ocr_page_tasks '
                    'WHERE job_id=:job AND page_num=:page LIMIT 2'),
                    {'job': source_job_id, 'page': page_num}).mappings().all()
                if len(rows) > 1:
                    raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
                if not self.authorize_identity(dict(identity)):
                    raise DomainError('SOURCE_OCR_UNAUTHORIZED')
                if rows:
                    # Never silently bind a task to a different staged page.
                    if rows[0]['local_image_path'] != str(path):
                        raise DomainError('SOURCE_OCR_STAGING_CONFLICT')
                    return rows[0]['id']
                if not self.contract_for(identity).allows(**permission, action='insert', fields=_TASK_COLUMNS):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                timestamp = datetime.now(UTC)
                task_id = SourceIDService(PostgreSQLSourceIDCounter(conn)).next_id('OPT', created_at=timestamp)
                conn.execute(text(
                    'INSERT INTO public.ocr_page_tasks (id, job_id, page_num, local_image_path, status, '
                    'retry_count, created_at, updated_at) VALUES '
                    "(:id, :job, :page, :path, 'PENDING', 0, :created, :created)"),
                    {'id': task_id, 'job': source_job_id, 'page': page_num, 'path': str(path), 'created': timestamp})
                if not self.authorize_identity(dict(identity)):
                    raise DomainError('SOURCE_OCR_UNAUTHORIZED')
                return task_id
        except SQLAlchemyError as exc:
            from data_sync_etl.adapters.source.postgresql_ocr_queue import source_sql_error
            raise source_sql_error(exc) from None

    def set_page_count(self, *, namespace, master_version_id, source_version_id, source_hash,
                       actor_id, role, source_user_id, source_job_id, total_pages):
        """Persist a verified page count once without resetting an existing job."""
        identity = dict(namespace=namespace, master_version_id=master_version_id,
                        source_version_id=source_version_id, source_hash=source_hash,
                        actor_id=actor_id, role=role, source_user_id=source_user_id)
        if (not isinstance(source_hash, str) or not _HASH.fullmatch(source_hash)
                or any(not isinstance(identity[key], str) or not 1 <= len(identity[key]) <= 30
                       for key in ('master_version_id', 'source_version_id', 'actor_id', 'source_user_id'))
                or not isinstance(source_job_id, str) or not 1 <= len(source_job_id) <= 30
                or type(total_pages) is not int or not 1 <= total_pages <= 2000
                or not self.authorize_identity(dict(identity))):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        try:
            with self.engine.begin() as conn:
                conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                             {'timeout': str(self.contract.statement_timeout_seconds * 1000)})
                version = conn.execute(text(
                    'SELECT id, source_hash, lifecycle_status FROM public.resource_versions '
                    'WHERE id=:id FOR UPDATE'), {'id': source_version_id}).mappings().one_or_none()
                if not version or not isinstance(version['source_hash'], str) or version['source_hash'].lower() != source_hash:
                    raise DomainError('SOURCE_VERSION_MUTATED')
                permission = dict(namespace=namespace, table='ocr_jobs', role=role, actor_id=actor_id,
                                  source_version_id=source_version_id, lifecycle=version['lifecycle_status'])
                if not self.contract_for(identity).allows(**permission, action='select', fields=_JOB_COLUMNS):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                actor = conn.execute(text('SELECT id, is_active FROM public.users WHERE id=:id'),
                                     {'id': source_user_id}).mappings().one_or_none()
                if not actor or actor['is_active'] is not True:
                    raise DomainError('SOURCE_OCR_ACTOR_INVALID')
                job = conn.execute(text(
                    'SELECT id, resource_version_id, status, total_pages FROM public.ocr_jobs '
                    'WHERE id=:id FOR UPDATE'), {'id': source_job_id}).mappings().one_or_none()
                if not job or job['resource_version_id'] != source_version_id:
                    raise DomainError('SOURCE_OCR_JOB_INVALID')
                if not self.authorize_identity(dict(identity)):
                    raise DomainError('SOURCE_OCR_UNAUTHORIZED')
                if job['total_pages'] == total_pages:
                    return
                if job['total_pages'] != 0 or job['status'] != 'QUEUED':
                    raise DomainError('SOURCE_OCR_PAGE_COUNT_CONFLICT')
                if not self.contract_for(identity).allows(**permission, action='update', fields={'total_pages', 'updated_at'}):
                    raise DomainError('SOURCE_OCR_CONTRACT_DENIED')
                conn.execute(text('UPDATE public.ocr_jobs SET total_pages=:total, updated_at=:updated WHERE id=:id'),
                             {'id': source_job_id, 'total': total_pages, 'updated': datetime.now(UTC)})
                if not self.authorize_identity(dict(identity)):
                    raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        except SQLAlchemyError as exc:
            from data_sync_etl.adapters.source.postgresql_ocr_queue import source_sql_error
            raise source_sql_error(exc) from None
