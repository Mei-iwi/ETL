"""Contract-driven CREATE_VERSION writer for SOURCE PostgreSQL.

This adapter is separate from the resource metadata writer because SOURCE may
grant metadata CREATE without granting version/file writes.  It is inert until
an owner-supplied ``entity_type=versions`` contract and a separate writer DSN
are configured.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from data_sync_etl.adapters.source.id_counter import PostgreSQLSourceIDCounter
from data_sync_etl.application.source_ids import SourceIDService
from data_sync_etl.domain.core import DomainError

_IDENTIFIER = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')
_SHA256 = re.compile(r'^[0-9a-fA-F]{64}$')

_REQUIRED_FIELDS = frozenset({
    'id', 'resource_id', 'parent_version_id', 'version_number', 'lifecycle_status',
    'storage_bucket', 'storage_object_key', 'source_hash', 'uploaded_by',
    'created_at', 'updated_at',
})


class DisabledSourceVersionWriter:
    supported = False
    create_supported = False

    def allows_create(self, **kwargs):
        return False

    def reserve_version_id(self, **kwargs):
        raise DomainError('SOURCE_VERSION_CREATE_DISABLED')

    def create_version(self, **kwargs):
        raise DomainError('SOURCE_VERSION_CREATE_DISABLED')


def _identifier(value, code='SOURCE_VERSION_CONTRACT_INVALID'):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DomainError(code)
    return value


class PostgreSQLSourceVersionWriter:
    supported = True

    def __init__(self, database_url: str, contract_file: Path, *, namespace='postgresql:learning',
                 connect_timeout_seconds=10, statement_timeout_seconds=30, lock_timeout_seconds=5,
                 allowed_actions=None):
        if not database_url or make_url(database_url).get_backend_name() != 'postgresql':
            raise DomainError('SOURCE_WRITE_DATABASE_URL_REQUIRED')
        try:
            contract = json.loads(Path(contract_file).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise DomainError('SOURCE_VERSION_CONTRACT_UNAVAILABLE') from exc
        if (not isinstance(contract, dict) or contract.get('backend') != 'postgresql'
                or contract.get('namespace') != namespace or contract.get('entity_type') != 'versions'):
            raise DomainError('SOURCE_VERSION_CONTRACT_MISMATCH')
        self.namespace = namespace
        self.schema = _identifier(contract.get('schema', 'public'))
        self.table = _identifier(contract.get('table'))
        if self.table != 'resource_versions':
            raise DomainError('SOURCE_VERSION_CONTRACT_UNSUPPORTED')
        self.id_column = _identifier(contract.get('id_column'))
        self.columns = {field: _identifier(column)
                        for field, column in (contract.get('columns') or {}).items()}
        self.state_columns = tuple(_identifier(column) for column in contract.get('state_columns', ()))
        required_state = {'id', 'resource_id', 'version_number', 'lifecycle_status',
                          'storage_bucket', 'storage_object_key', 'source_hash', 'updated_at'}
        if not _REQUIRED_FIELDS.issubset(self.columns) or self.columns.get('id') != self.id_column:
            raise DomainError('SOURCE_VERSION_CREATE_CONTRACT_INCOMPLETE')
        if not required_state.issubset(self.state_columns):
            raise DomainError('SOURCE_VERSION_CONTRACT_INCOMPLETE')
        actions = contract.get('actions', [])
        if not isinstance(actions, list) or not actions or not set(actions).issubset({'create'}):
            raise DomainError('SOURCE_VERSION_CONTRACT_INVALID')
        self.actions = frozenset(actions)
        if allowed_actions is not None and not self.actions.issubset(frozenset(allowed_actions)):
            raise DomainError('SOURCE_WRITE_ACTIONS_CONFIGURATION_MISMATCH')
        allowed_roles = contract.get('allowed_roles', [])
        allowed_actors = contract.get('allowed_actor_ids', [])
        if (not isinstance(allowed_roles, list) or not isinstance(allowed_actors, list)
                or any(not isinstance(value, str) or not value.strip()
                       for value in (*allowed_roles, *allowed_actors))):
            raise DomainError('SOURCE_VERSION_CONTRACT_INVALID')
        self.allowed_roles = frozenset(value.strip().upper() for value in allowed_roles)
        self.allowed_actor_ids = frozenset(value.strip() for value in allowed_actors)
        self.grant_configured = bool(self.allowed_roles or self.allowed_actor_ids)
        self.id_prefix = contract.get('id_prefix')
        if not isinstance(self.id_prefix, str) or not re.fullmatch(r'[A-Z]{2,5}', self.id_prefix):
            raise DomainError('SOURCE_VERSION_ID_PREFIX_UNCONFIRMED')
        self.id_pattern = contract.get('id_pattern')
        if not isinstance(self.id_pattern, str) or len(self.id_pattern) > 120:
            raise DomainError('SOURCE_VERSION_ID_PATTERN_UNCONFIRMED')
        try:
            self._id_re = re.compile(self.id_pattern)
        except re.error as exc:
            raise DomainError('SOURCE_VERSION_ID_PATTERN_UNCONFIRMED') from exc
        statuses = contract.get('allowed_lifecycle_statuses')
        if (not isinstance(statuses, list) or not statuses
                or any(not isinstance(value, str) or not value.strip() for value in statuses)):
            raise DomainError('SOURCE_VERSION_LIFECYCLE_CONTRACT_REQUIRED')
        self.allowed_lifecycle_statuses = frozenset(value.strip().upper() for value in statuses)
        self.connect_timeout_seconds = connect_timeout_seconds
        self.statement_timeout_seconds = statement_timeout_seconds
        self.lock_timeout_seconds = lock_timeout_seconds
        self.engine = create_engine(
            database_url, hide_parameters=True, pool_pre_ping=True, pool_size=1,
            max_overflow=0, pool_recycle=max(60, connect_timeout_seconds * 6),
            connect_args={'connect_timeout': connect_timeout_seconds},
        )

    @property
    def create_supported(self):
        return 'create' in self.actions and bool(self.grant_configured)

    def supports(self, locator):
        return (locator.backend.casefold() == 'postgresql'
                and locator.namespace == self.namespace and locator.entity_type == 'versions')

    def allows_create(self, *, actor_id, role):
        return bool(self.grant_configured and self.create_supported
                    and (str(role).upper() in self.allowed_roles or actor_id in self.allowed_actor_ids))

    def reserve_version_id(self, *, created_at: datetime):
        if not self.create_supported:
            raise DomainError('SOURCE_VERSION_CREATE_ACTION_BLOCKED')
        with self.engine.begin() as connection:
            self._set_timeouts(connection)
            value = SourceIDService(PostgreSQLSourceIDCounter(connection)).next_id(
                self.id_prefix, created_at=created_at)
        if not self._id_re.fullmatch(value):
            raise DomainError('SOURCE_VERSION_ID_CONTRACT_MISMATCH')
        return value

    def create_version(self, *, source_version_id, source_resource_id, parent_version_id,
                       version_number, lifecycle_status, storage_bucket, storage_object_key,
                       source_hash, uploaded_by, created_at, idempotency_key):
        if not self.create_supported:
            raise DomainError('SOURCE_VERSION_CREATE_ACTION_BLOCKED')
        if not isinstance(source_version_id, str) or not self._id_re.fullmatch(source_version_id):
            raise DomainError('SOURCE_VERSION_ID_INVALID')
        if not isinstance(source_resource_id, str) or not 1 <= len(source_resource_id) <= 30:
            raise DomainError('SOURCE_RESOURCE_ID_INVALID')
        if parent_version_id is not None and (not isinstance(parent_version_id, str) or not 1 <= len(parent_version_id) <= 30):
            raise DomainError('SOURCE_VERSION_PARENT_INVALID')
        if version_number is not None and (type(version_number) is not int or version_number < 1):
            raise DomainError('SOURCE_VERSION_NUMBER_INVALID')
        if lifecycle_status not in self.allowed_lifecycle_statuses:
            raise DomainError('SOURCE_VERSION_LIFECYCLE_INVALID')
        if not isinstance(storage_bucket, str) or not storage_bucket or len(storage_bucket) > 255:
            raise DomainError('SOURCE_VERSION_STORAGE_INVALID')
        if not isinstance(storage_object_key, str) or not storage_object_key or len(storage_object_key) > 500:
            raise DomainError('SOURCE_VERSION_STORAGE_INVALID')
        if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
            raise DomainError('SOURCE_VERSION_HASH_INVALID')
        if not isinstance(uploaded_by, str) or not 1 <= len(uploaded_by) <= 30:
            raise DomainError('SOURCE_VERSION_ACTOR_MAPPING_REQUIRED')
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            raise DomainError('SOURCE_VERSION_TIMESTAMP_INVALID')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', idempotency_key):
            raise DomainError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID')
        values = {
            'id': source_version_id, 'resource_id': source_resource_id,
            'parent_version_id': parent_version_id, 'version_number': version_number,
            'lifecycle_status': lifecycle_status, 'storage_bucket': storage_bucket,
            'storage_object_key': storage_object_key, 'source_hash': source_hash.lower(),
            'uploaded_by': uploaded_by, 'created_at': created_at, 'updated_at': created_at,
        }
        with self.engine.begin() as connection:
            self._set_timeouts(connection)
            resource = connection.execute(text(
                f'SELECT id, is_active, deleted_at FROM "{self.schema}"."learning_resources" '
                'WHERE id=:resource_id FOR UPDATE'), {'resource_id': source_resource_id}).mappings().one_or_none()
            if resource is None or resource['is_active'] is not True or resource['deleted_at'] is not None:
                raise DomainError('SOURCE_RESOURCE_NOT_WRITABLE')
            latest_row = connection.execute(text(
                f'SELECT "{self.columns["id"]}" AS id, "{self.columns["version_number"]}" AS version_number '
                f'FROM "{self.schema}"."{self.table}" WHERE "{self.columns["resource_id"]}"=:resource_id '
                f'ORDER BY "{self.columns["version_number"]}" DESC LIMIT 1 FOR UPDATE'),
                {'resource_id': source_resource_id}).mappings().one_or_none()
            if version_number is None:
                values['version_number'] = (int(latest_row['version_number']) + 1) if latest_row else 1
            if parent_version_id is None and latest_row:
                values['parent_version_id'] = latest_row['id']
            existing = self._canonical_row(connection, source_version_id)
            expected = {key: values[key] for key in ('id', 'resource_id', 'parent_version_id', 'version_number',
                                                       'lifecycle_status', 'storage_bucket',
                                                       'storage_object_key', 'source_hash', 'uploaded_by')}
            if existing is not None:
                actual = {key: existing.get(key) for key in expected}
                if actual != expected:
                    raise DomainError('SOURCE_VERSION_IDEMPOTENCY_CONFLICT')
                return {'status': 'APPLIED', 'source_version_id': source_version_id,
                        'source_state_hash': self._hash_row(existing), 'idempotent_replay': True}
            latest = latest_row['version_number'] if latest_row else None
            if latest is not None and values['version_number'] != int(latest) + 1:
                raise DomainError('SOURCE_VERSION_NUMBER_CONFLICT')
            columns = ', '.join(f'"{self.columns[field]}"' for field in values)
            binds = ', '.join(f':{field}' for field in values)
            connection.execute(text(
                f'INSERT INTO "{self.schema}"."{self.table}" ({columns}) VALUES ({binds})'), values)
            created = self._canonical_row(connection, source_version_id)
            if created is None:
                raise DomainError('SOURCE_VERSION_READBACK_FAILED')
            return {'status': 'APPLIED', 'source_version_id': source_version_id,
                    'source_state_hash': self._hash_row(created), 'idempotent_replay': False}

    def _set_timeouts(self, connection):
        connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                           {'timeout': f'{self.statement_timeout_seconds}s'})
        connection.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                           {'timeout': f'{self.lock_timeout_seconds}s'})

    def _canonical_row(self, connection, source_version_id):
        selected = ', '.join(
            f'v."{self.columns[field]}" AS "{field}"' for field in _REQUIRED_FIELDS
            if field in self.columns)
        return connection.execute(text(
            f'SELECT {selected} FROM "{self.schema}"."{self.table}" v '
            f'WHERE v."{self.id_column}"=:source_version_id'),
            {'source_version_id': source_version_id}).mappings().one_or_none()

    @staticmethod
    def _hash_row(row):
        import hashlib
        return hashlib.sha256(json.dumps(dict(row), default=str, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest()
