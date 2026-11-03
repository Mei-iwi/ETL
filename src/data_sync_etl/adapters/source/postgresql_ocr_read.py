"""Read existing SOURCE OCR rows without creating schema or changing SOURCE."""
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from data_sync_etl.domain.core import DomainError

_SOURCE_JOB_STATUS = {
    'QUEUED': 'PENDING', 'PROCESSING': 'RUNNING', 'POSTPROCESSING': 'RUNNING',
    'COMPLETED': 'COMPLETED', 'FAILED': 'FAILED', 'CANCELLED': 'CANCELLED',
}
_SOURCE_PAGE_STATUS = {
    'PENDING': 'PENDING', 'PROCESSING': 'RUNNING', 'COMPLETED': 'SUCCEEDED',
    'FAILED': 'FAILED', 'CANCELLED': 'CANCELLED',
}
_SOURCE_REVIEW_STATUS = {'PENDING': 'UNREVIEWED', 'APPROVED': 'APPROVED', 'REJECTED': 'REJECTED'}


class PostgreSQLSourceOCRReader:
    """Read metadata only; SOURCE identity must be supplied by trusted mapping."""

    def __init__(self, engine, *, authorize, lifecycle_allowed, statement_timeout_seconds=15):
        if not callable(authorize) or not callable(lifecycle_allowed):
            raise ValueError('SOURCE_OCR_AUTHORIZER_REQUIRED')
        self.engine, self.authorize, self.lifecycle_allowed = engine, authorize, lifecycle_allowed
        if type(statement_timeout_seconds) is not int or not 1 <= statement_timeout_seconds <= 600:
            raise ValueError('SOURCE_OCR_TIMEOUT_INVALID')
        self.statement_timeout_seconds = statement_timeout_seconds

    def snapshot(self, *, namespace, source_version_id, master_version_id, source_hash):
        if (not isinstance(namespace, str) or not 1 <= len(namespace) <= 255 or not isinstance(source_version_id, str)
                or not 1 <= len(source_version_id) <= 30 or not isinstance(master_version_id, str)
                or not 1 <= len(master_version_id) <= 30 or not isinstance(source_hash, str)
                or not re.fullmatch(r'[0-9a-f]{64}', source_hash)):
            raise DomainError('SOURCE_OCR_INVALID_IDENTITY')
        identity = dict(namespace=namespace, source_version_id=source_version_id,
                        master_version_id=master_version_id, source_hash=source_hash)
        if not self.authorize(identity):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        try:
            with self.engine.begin() as conn:
                if conn.dialect.name == 'postgresql':
                    conn.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
                    conn.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                                 {'timeout': str(self.statement_timeout_seconds * 1000)})
                prefix = 'public.' if conn.dialect.name == 'postgresql' else ''
                version = conn.execute(text(
                    f'SELECT id, source_hash, lifecycle_status, updated_at FROM {prefix}resource_versions WHERE id = :id'
                ), {'id': source_version_id}).mappings().one_or_none()
                if version is None:
                    raise DomainError('SOURCE_VERSION_MISSING')
                if not isinstance(version['source_hash'], str) or version['source_hash'].lower() != source_hash:
                    raise DomainError('SOURCE_VERSION_MUTATED')
                if not self.lifecycle_allowed(version['lifecycle_status']):
                    raise DomainError('SOURCE_OCR_LIFECYCLE_BLOCKED')
                jobs = conn.execute(text(
                    'SELECT id, resource_version_id, triggered_by, created_at, status, total_pages, completed_pages, failed_pages, '
                    f'started_at, completed_at, updated_at FROM {prefix}ocr_jobs WHERE resource_version_id = :id LIMIT 2'
                ), {'id': source_version_id}).mappings().all()
                if len(jobs) > 1:
                    raise DomainError('SOURCE_OCR_JOB_CONFLICT')
                if not jobs:
                    result = {'source_version_id': source_version_id, 'job': None, 'pages': []}
                else:
                    job = dict(jobs[0])
                    if (not isinstance(job['id'], str) or not 1 <= len(job['id']) <= 30
                            or job['status'] not in _SOURCE_JOB_STATUS
                            or type(job['total_pages']) is not int or not 0 <= job['total_pages'] <= 2000
                            or any(type(job[key]) is not int or job[key] < 0 for key in ('completed_pages', 'failed_pages'))
                            or job['completed_pages'] + job['failed_pages'] > job['total_pages']):
                        raise DomainError('SOURCE_OCR_INVALID_STATE')
                    rows = conn.execute(text(
                        'SELECT id, job_id, page_num, status, retry_count, updated_at, started_at, heartbeat_at, completed_at '
                        f'FROM {prefix}ocr_page_tasks WHERE job_id = :id ORDER BY page_num, id LIMIT 2001'
                    ), {'id': job['id']}).mappings().all()
                    if len(rows) > 2000:
                        raise DomainError('SOURCE_OCR_LIMIT_EXCEEDED')
                    pages, seen = [], set()
                    for row in rows:
                        page = dict(row)
                        page.setdefault('started_at', None)
                        page.setdefault('heartbeat_at', None)
                        page.setdefault('completed_at', None)
                        if (not isinstance(page['id'], str) or not 1 <= len(page['id']) <= 30
                                or type(page['page_num']) is not int or not 1 <= page['page_num'] <= job['total_pages']
                                or page['page_num'] in seen or page['status'] not in _SOURCE_PAGE_STATUS
                                or type(page['retry_count']) is not int or page['retry_count'] < 0):
                            raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
                        seen.add(page['page_num'])
                        page['status'] = _SOURCE_PAGE_STATUS[page['status']]
                        pages.append(page)
                    job['status'] = _SOURCE_JOB_STATUS[job['status']]
                    result = {'source_version_id': source_version_id, 'job': job, 'pages': pages}
                rows = conn.execute(text(
                    'SELECT id, mongo_id, resource_version_id, parent_unit_id, unit_type, title, sequence_no, '
                    f'page_from, page_to, review_status, retrieval_eligible, updated_at FROM {prefix}content_units '
                    'WHERE resource_version_id = :id ORDER BY sequence_no, id LIMIT 10001'
                ), {'id': source_version_id}).mappings().all()
                if len(rows) > 10000:
                    raise DomainError('SOURCE_OCR_LIMIT_EXCEEDED')
                units, unit_ids = [], set()
                for row in rows:
                    unit = dict(row)
                    unit.setdefault('title', None)
                    if (not isinstance(unit['id'], str) or not 1 <= len(unit['id']) <= 30 or unit['id'] in unit_ids
                            or (unit['title'] is not None and (not isinstance(unit['title'], str) or len(unit['title']) > 500))
                            or unit['unit_type'] not in ('PART', 'CHAPTER', 'LESSON', 'SECTION', 'SUBSECTION')
                            or unit['review_status'] not in _SOURCE_REVIEW_STATUS
                            or type(unit['sequence_no']) is not int or unit['sequence_no'] < 1
                            or type(unit['retrieval_eligible']) not in (bool, int) or unit['retrieval_eligible'] not in (0, 1)):
                        raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
                    unit['retrieval_eligible'] = bool(unit['retrieval_eligible'])
                    unit['review_status'] = _SOURCE_REVIEW_STATUS[unit['review_status']]
                    if (any(unit[key] is not None and (type(unit[key]) is not int or unit[key] < 1)
                            for key in ('page_from', 'page_to'))
                            or (unit['page_from'] and unit['page_to'] and unit['page_from'] > unit['page_to'])):
                        raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
                    unit_ids.add(unit['id'])
                    units.append(unit)
                parents = {unit['id']: unit['parent_unit_id'] for unit in units}
                resolved = set()
                for unit in units:
                    seen, parent = set(), unit['id']
                    while parent is not None and parent not in resolved:
                        if parent not in parents or parent in seen:
                            raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
                        seen.add(parent)
                        parent = parents[parent]
                    resolved.update(seen)
                result['units'] = units
        except SQLAlchemyError as exc:
            from data_sync_etl.adapters.source.postgresql_ocr_queue import source_sql_error
            raise source_sql_error(exc) from None
        if not self.authorize(identity):
            raise DomainError('SOURCE_OCR_UNAUTHORIZED')
        return {**result, 'namespace': namespace, 'master_version_id': master_version_id, 'source_hash': source_hash}
