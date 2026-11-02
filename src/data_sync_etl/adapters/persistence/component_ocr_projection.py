"""Explicitly invoked component-to-Master projection; no worker is registered.

The caller owns the transaction and must authenticate the component event and
verify its Master/source identity mapping.  A committed transaction may be
replayed before the component cursor is acknowledged.
"""
import re
from datetime import datetime, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from data_sync_etl.db.ocr_component_models import (
    ComponentJobRef,
    ComponentPageRef,
    ComponentUnitRef,
)

_MODELS = {'job': ComponentJobRef, 'page': ComponentPageRef, 'unit': ComponentUnitRef}
_ID = {'job': 'source_job_id', 'page': 'source_task_id', 'unit': 'source_unit_id'}
_CODE = re.compile(r'^[A-Z0-9_]{1,80}$')
_HASH = re.compile(r'^[0-9a-f]{64}$')


class ProjectionError(ValueError):
    pass


class ComponentOCRProjector:
    def __init__(self, connection, verify_mapping):
        self.connection = connection
        self.verify_mapping = verify_mapping

    def apply(self, kind, snapshot):
        """Apply one trusted snapshot using an atomic monotonic revision upsert.

        Unknown fields are rejected, rather than copied: text, paths, secrets
        and unrestricted error messages must never enter a Master replica.
        """
        if kind not in _MODELS or not isinstance(snapshot, dict):
            raise ProjectionError('INVALID_SNAPSHOT')
        model = _MODELS[kind]
        table = model.__table__
        fields = set(table.c.keys()) - {'synced_at'}
        if set(snapshot) != fields:
            raise ProjectionError('INVALID_FIELDS')
        data = dict(snapshot)
        for key in ('namespace', _ID[kind], 'master_version_id', 'source_version_id',
                    'source_job_id', 'master_resource_id'):
            if key not in data:
                continue
            if not isinstance(data[key], str) or not data[key].strip():
                raise ProjectionError('INVALID_IDENTITY')
        for key in ('generation', 'revision'):
            if type(data[key]) is not int or data[key] < 1:
                raise ProjectionError('INVALID_REVISION')
        for key in ('source_hash', 'processing_fingerprint', 'text_checksum'):
            if key in data and (not isinstance(data[key], str) or not _HASH.fullmatch(data[key])):
                raise ProjectionError('INVALID_CHECKSUM')
        for key in ('status', 'unit_type', 'review_status'):
            if key in data and (not isinstance(data[key], str) or not _CODE.fullmatch(data[key])):
                raise ProjectionError('INVALID_STATUS')
        if kind == 'page' and data['error_code'] is not None:
            if not isinstance(data['error_code'], str) or not _CODE.fullmatch(data['error_code']):
                raise ProjectionError('INVALID_ERROR_CODE')
        for key in ('tombstone', 'verified', 'retrieval_eligible'):
            if key in data and type(data[key]) is not bool:
                raise ProjectionError('INVALID_BOOLEAN')
        for key in ('total_pages', 'completed_pages', 'failed_pages', 'retry_count', 'sequence_no'):
            if key in data and (type(data[key]) is not int or data[key] < 0):
                raise ProjectionError('INVALID_COUNTER')
        if kind == 'page' and (type(data['page_num']) is not int or data['page_num'] < 1):
            raise ProjectionError('INVALID_PAGE')
        if kind == 'job' and data['completed_pages'] + data['failed_pages'] > data['total_pages']:
            raise ProjectionError('INVALID_COUNTER')
        if kind == 'unit':
            if data['retrieval_eligible'] and not data['verified']:
                raise ProjectionError('UNIT_NOT_VERIFIED')
            for key in ('page_from', 'page_to'):
                if data[key] is not None and (type(data[key]) is not int or data[key] < 1):
                    raise ProjectionError('INVALID_PAGE')
            if data['page_from'] and data['page_to'] and data['page_from'] > data['page_to']:
                raise ProjectionError('INVALID_PAGE')
        if not self.verify_mapping(kind, dict(data)):
            raise ProjectionError('IDENTITY_NOT_AUTHORIZED')
        if kind == 'page':
            parent = self.connection.execute(select(ComponentJobRef.__table__).where(
                ComponentJobRef.namespace == data['namespace'],
                ComponentJobRef.source_job_id == data['source_job_id'],
                ComponentJobRef.generation == data['generation'],
            )).mappings().one_or_none()
            if (parent is None or parent['master_version_id'] != data['master_version_id']
                    or parent['tombstone'] or data['page_num'] > parent['total_pages']):
                raise ProjectionError('PARENT_NOT_READY')
        if kind == 'unit' and data['parent_unit_id'] is not None:
            parent_id = data['parent_unit_id']
            visited = {data['source_unit_id']}
            while parent_id is not None:
                if not isinstance(parent_id, str) or parent_id in visited or len(visited) >= 1000:
                    raise ProjectionError('PARENT_NOT_READY')
                visited.add(parent_id)
                parent = self.connection.execute(select(ComponentUnitRef.__table__).where(
                    ComponentUnitRef.namespace == data['namespace'],
                    ComponentUnitRef.source_unit_id == parent_id,
                    ComponentUnitRef.generation == data['generation'],
                )).mappings().one_or_none()
                if (parent is None or parent['master_version_id'] != data['master_version_id']
                        or parent['source_version_id'] != data['source_version_id'] or parent['tombstone']):
                    raise ProjectionError('PARENT_NOT_READY')
                parent_id = parent['parent_unit_id']
        data['synced_at'] = datetime.now(timezone.utc)
        dialect = self.connection.dialect.name
        if dialect not in ('postgresql', 'sqlite'):
            raise ProjectionError('UNSUPPORTED_DATABASE')
        statement = (pg_insert if dialect == 'postgresql' else sqlite_insert)(table).values(**data)
        key = ['namespace', _ID[kind], 'generation']
        immutable = {'namespace', _ID[kind], 'generation', 'master_version_id'}
        immutable.update({'source_version_id', 'master_resource_id'} & fields)
        if kind == 'job':
            immutable.update({'source_hash', 'processing_fingerprint'})
        if kind == 'page':
            immutable.add('source_job_id')
        excluded = statement.excluded
        same_identity = [table.c[name] == excluded[name] for name in immutable]
        terminal_fence = or_(table.c.status != 'COMPLETED', excluded.status == 'COMPLETED',
                             excluded.tombstone.is_(True)) if kind == 'job' else True
        update = {name: excluded[name] for name in fields - immutable}
        update['synced_at'] = excluded.synced_at
        statement = statement.on_conflict_do_update(
            index_elements=[table.c[name] for name in key], set_=update,
            where=(table.c.revision < excluded.revision) & and_(*same_identity)
                  & or_(table.c.tombstone.is_(False), excluded.tombstone.is_(True)) & terminal_fence,
        )
        self.connection.execute(statement)
        current = self.connection.execute(select(table).where(
            *[table.c[name] == data[name] for name in key])).mappings().one()
        if any(current[name] != data[name] for name in immutable):
            raise ProjectionError('IDENTITY_CONFLICT')
        if current['tombstone'] and not data['tombstone'] and current['revision'] < data['revision']:
            raise ProjectionError('TOMBSTONE_CONFLICT')
        if (kind == 'job' and current['status'] == 'COMPLETED' and data['status'] != 'COMPLETED'
                and not data['tombstone'] and current['revision'] < data['revision']):
            raise ProjectionError('TERMINAL_JOB_CONFLICT')
        if current['revision'] == data['revision']:
            if any(current[name] != data[name] for name in fields):
                raise ProjectionError('REVISION_CONFLICT')
        return current['revision'] == data['revision']
