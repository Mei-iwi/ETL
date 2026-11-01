"""Contract-driven PostgreSQL SOURCE metadata writer.

The adapter is intentionally inert until a SOURCE owner supplies an exact
contract file and a separate RW connection.  It never infers tables/columns,
creates schema objects, widens grants, or accepts SQL from an API caller.
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
from data_sync_etl.domain.source_ownership import state_hash, validate_source_writeback_fields

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")

_RESOURCE_STATE_COLUMNS = frozenset({
    'id', 'mongo_id', 'provider_id', 'resource_type_id', 'created_by', 'title',
    'publication_status', 'is_active', 'deleted_at', 'deleted_by', 'created_at',
    'updated_at', 'provider_name', 'provider_type', 'provider_is_active',
    'provider_deleted_at', 'provider_deleted_by', 'provider_created_at',
    'provider_updated_at', 'resource_type_code', 'resource_type_name_vi',
    'resource_type_is_active', 'resource_type_deleted_at', 'resource_type_deleted_by',
    'resource_type_created_at', 'resource_type_updated_at',
})

_RESOURCE_CREATE_FIELDS = frozenset({
    'id', 'provider_id', 'resource_type_id', 'created_by', 'title',
    'publication_status', 'is_active', 'created_at', 'updated_at',
})


def _identifier(value: str, code: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise DomainError(code)
    return value


class PostgreSQLSourceMetadataWriter:
    supported = True

    def __init__(self, database_url: str, contract_file: Path, *, namespace: str = 'postgresql:learning',
                 connect_timeout_seconds: int = 10, statement_timeout_seconds: int = 30,
                 lock_timeout_seconds: int = 5, allowed_actions=None):
        if not database_url or make_url(database_url).get_backend_name() != 'postgresql':
            raise DomainError('SOURCE_WRITE_DATABASE_URL_REQUIRED')
        if not 1 <= connect_timeout_seconds <= 120:
            raise DomainError('SOURCE_WRITE_TIMEOUT_INVALID')
        if not 1 <= statement_timeout_seconds <= 600 or not 1 <= lock_timeout_seconds <= 120:
            raise DomainError('SOURCE_WRITE_TIMEOUT_INVALID')
        self.namespace = namespace
        try:
            contract = json.loads(Path(contract_file).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise DomainError('SOURCE_WRITE_CONTRACT_UNAVAILABLE') from exc
        if not isinstance(contract, dict) or contract.get('backend') != 'postgresql' \
                or contract.get('namespace') != namespace or contract.get('entity_type') != 'resources':
            raise DomainError('SOURCE_WRITE_CONTRACT_MISMATCH')
        self.schema = _identifier(contract.get('schema', 'public'), 'SOURCE_WRITE_CONTRACT_INVALID')
        self.table = _identifier(contract.get('table'), 'SOURCE_WRITE_CONTRACT_INVALID')
        if self.table != 'learning_resources':
            raise DomainError('SOURCE_WRITE_CONTRACT_UNSUPPORTED')
        self.id_column = _identifier(contract.get('id_column'), 'SOURCE_WRITE_CONTRACT_INVALID')
        self.state_columns = tuple(_identifier(col, 'SOURCE_WRITE_CONTRACT_INVALID') for col in contract.get('state_columns', ()))
        self.columns = {field: _identifier(column, 'SOURCE_WRITE_CONTRACT_INVALID')
                        for field, column in (contract.get('columns') or {}).items()}
        idempotency_column = contract.get('idempotency_column')
        self.idempotency_column = (_identifier(idempotency_column, 'SOURCE_WRITE_CONTRACT_INVALID')
                                   if idempotency_column is not None else None)
        actions = contract.get('actions', ['update'])
        if not isinstance(actions, list) or not actions or not set(actions).issubset({'create', 'update', 'delete'}):
            raise DomainError('SOURCE_WRITE_CONTRACT_INVALID')
        self.actions = frozenset(actions)
        if allowed_actions is not None:
            configured_actions = frozenset(allowed_actions)
            if not configured_actions or not self.actions.issubset(configured_actions):
                raise DomainError('SOURCE_WRITE_ACTIONS_CONFIGURATION_MISMATCH')
        allowed_roles = contract.get('allowed_roles', [])
        allowed_actors = contract.get('allowed_actor_ids', [])
        if (not isinstance(allowed_roles, list) or not isinstance(allowed_actors, list)
                or any(not isinstance(value, str) or not value.strip() for value in (*allowed_roles, *allowed_actors))):
            raise DomainError('SOURCE_WRITE_CONTRACT_INVALID')
        self.allowed_roles = frozenset(value.strip().upper() for value in allowed_roles)
        self.allowed_actor_ids = frozenset(value.strip() for value in allowed_actors)
        self.grant_configured = bool(self.allowed_roles or self.allowed_actor_ids)
        if not _RESOURCE_STATE_COLUMNS.issubset(self.state_columns) or 'title' not in self.columns:
            raise DomainError('SOURCE_WRITE_CONTRACT_INCOMPLETE')
        if 'create' in self.actions:
            if (not _RESOURCE_CREATE_FIELDS.issubset(self.columns)
                    or self.columns.get('id') != self.id_column):
                raise DomainError('SOURCE_WRITE_CREATE_CONTRACT_INCOMPLETE')
        self.connect_timeout_seconds = connect_timeout_seconds
        self.statement_timeout_seconds = statement_timeout_seconds
        self.lock_timeout_seconds = lock_timeout_seconds
        self.engine = create_engine(
            database_url,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_size=1,
            max_overflow=0,
            pool_recycle=max(60, connect_timeout_seconds * 6),
            connect_args={'connect_timeout': connect_timeout_seconds},
        )

    def supports(self, locator):
        return (locator.backend.casefold() == 'postgresql' and locator.namespace == self.namespace
                and locator.entity_type == 'resources')

    def allows(self, *, actor_id, role, resource, locator, fields):
        """Evaluate only the owner-supplied allow-list, never role alone."""
        return (self.grant_configured and self.supports(locator)
                and (role.upper() in self.allowed_roles or actor_id in self.allowed_actor_ids)
                and set(fields).issubset(self.columns)
                and 'update' in self.actions)

    @property
    def create_supported(self):
        return 'create' in self.actions and _RESOURCE_CREATE_FIELDS.issubset(self.columns)

    def allows_create(self, *, actor_id, role, source_user_id):
        """Return the contract decision for a metadata-only SOURCE CREATE.

        The actor-to-SOURCE user mapping is resolved by the application layer;
        this method only evaluates the owner-supplied role/actor allow-list and
        never creates or guesses a SOURCE identity.
        """
        return bool(self.grant_configured and self.create_supported and source_user_id
                    and (role.upper() in self.allowed_roles or actor_id in self.allowed_actor_ids))

    @property
    def delete_supported(self):
        """Whether the owner contract explicitly permits a soft delete.

        The contract must map every state column used by the operation.  Merely
        having ``is_active``/``deleted_at`` in the read projection is not a
        write grant and therefore never enables this property.
        """
        required = {'is_active', 'deleted_at', 'deleted_by', 'updated_at'}
        return 'delete' in self.actions and required.issubset(self.columns)

    def allows_delete(self, *, actor_id, role, source_user_id):
        return bool(self.grant_configured and self.delete_supported and source_user_id
                    and (role.upper() in self.allowed_roles or actor_id in self.allowed_actor_ids))

    def reserve_resource_id(self, *, created_at: datetime) -> str:
        """Reserve a SOURCE RES id in its own committed counter transaction.

        A counter gap is safe and preferable to an untracked resource.  The
        caller persists the returned id in the MASTER audit checkpoint before
        invoking CREATE, which makes retries use the exact same id after a
        SOURCE commit/MASTER crash boundary.
        """
        if not self.create_supported:
            raise DomainError('SOURCE_CREATE_ACTION_BLOCKED')
        with self.engine.begin() as connection:
            connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                               {'timeout': f'{self.statement_timeout_seconds}s'})
            connection.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                               {'timeout': f'{self.lock_timeout_seconds}s'})
            return SourceIDService(PostgreSQLSourceIDCounter(connection)).next_id(
                'RES', created_at=created_at)

    def create_resource(self, *, source_resource_id, source_user_id, provider_id,
                        resource_type_id, title, publication_status, subject_id=None,
                        grade_level_id=None, created_at, idempotency_key):
        """Create SOURCE metadata and relations in one transaction.

        The resource id is allocated and durably checkpointed before this call.
        If the SOURCE commit succeeded but MASTER did not, retrying the same
        checkpoint reads the existing id and verifies the canonical payload;
        it never allocates a second resource for the same job.
        """
        if not self.create_supported:
            raise DomainError('SOURCE_CREATE_ACTION_BLOCKED')
        if (not isinstance(source_resource_id, str)
                or not re.fullmatch(r'RES(?:0[1-9]|1[0-2])[0-9]{7}', source_resource_id)):
            raise DomainError('SOURCE_CREATE_RESOURCE_ID_INVALID')
        if not isinstance(source_user_id, str) or not 1 <= len(source_user_id) <= 30:
            raise DomainError('SOURCE_CREATE_ACTOR_MAPPING_REQUIRED')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', idempotency_key):
            raise DomainError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID')
        if not isinstance(title, str) or not title.strip() or len(title.strip()) > 500:
            raise DomainError('SOURCE_CREATE_TITLE_INVALID')
        if publication_status not in ('DRAFT', 'PUBLISHED', 'ARCHIVED'):
            raise DomainError('SOURCE_CREATE_PUBLICATION_STATUS_INVALID')
        if not isinstance(created_at, datetime) or created_at.tzinfo is None:
            raise DomainError('SOURCE_CREATE_TIMESTAMP_INVALID')
        values = {
            'id': source_resource_id, 'provider_id': provider_id,
            'resource_type_id': resource_type_id, 'created_by': source_user_id,
            'title': title.strip(), 'publication_status': publication_status,
            'is_active': True, 'created_at': created_at, 'updated_at': created_at,
        }
        with self.engine.begin() as connection:
            connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                               {'timeout': f'{self.statement_timeout_seconds}s'})
            connection.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                               {'timeout': f'{self.lock_timeout_seconds}s'})
            actor = connection.execute(text(
                f'SELECT id FROM "{self.schema}"."users" '
                'WHERE id=:id AND is_active=true'), {'id': source_user_id}).scalar_one_or_none()
            if actor is None:
                raise DomainError('SOURCE_CREATE_ACTOR_MAPPING_INVALID')
            provider = connection.execute(text(
                f'SELECT id FROM "{self.schema}"."providers" '
                'WHERE id=:id AND is_active=true AND deleted_at IS NULL'), {'id': provider_id}).scalar_one_or_none()
            if provider is None:
                raise DomainError('SOURCE_CREATE_PROVIDER_INVALID')
            resource_type = connection.execute(text(
                f'SELECT id FROM "{self.schema}"."resource_types" '
                'WHERE id=:id AND is_active=true AND deleted_at IS NULL'), {'id': resource_type_id}).scalar_one_or_none()
            if resource_type is None:
                raise DomainError('SOURCE_CREATE_RESOURCE_TYPE_INVALID')
            existing = self._canonical_row(connection, source_resource_id, lock=True)
            if existing is not None:
                expected = {key: values[key] for key in ('id', 'provider_id', 'resource_type_id', 'created_by', 'title',
                                                          'publication_status', 'is_active')}
                actual = {key: existing.get(key) for key in expected}
                if actual != expected:
                    raise DomainError('SOURCE_CREATE_IDEMPOTENCY_CONFLICT')
                self._ensure_relations(connection, source_resource_id, subject_id, grade_level_id)
                return {'status': 'APPLIED', 'source_resource_id': source_resource_id,
                        'source_state_hash': state_hash(self._canonical_state(existing)),
                        'idempotent_replay': True}
            columns = ', '.join(f'"{self.columns[field]}"' for field in values)
            connection.execute(text(
                f'INSERT INTO "{self.schema}"."{self.table}" ({columns}) VALUES '
                f'({", ".join(f":{field}" for field in values)})'), values)
            self._ensure_relations(connection, source_resource_id, subject_id, grade_level_id)
            created = self._canonical_row(connection, source_resource_id, lock=False)
            if created is None:
                raise DomainError('SOURCE_CREATE_READBACK_FAILED')
            return {'status': 'APPLIED', 'source_resource_id': source_resource_id,
                    'source_state_hash': state_hash(self._canonical_state(created)),
                    'idempotent_replay': False}

    def delete_resource(self, *, locator, expected_source_state_hash, deleted_by,
                        idempotency_key):
        """Soft-delete a SOURCE resource under the explicit delete contract.

        Physical rows, versions and objects are never removed.  A replay reads
        the canonical row and returns the resulting state, making an uncertain
        SOURCE commit safe to retry without a second mutation.
        """
        if 'delete' not in self.actions or not self.delete_supported:
            raise DomainError('SOURCE_DELETE_ACTION_BLOCKED')
        if not self.supports(locator):
            raise DomainError('SOURCE_WRITEBACK_BLOCKED')
        if not isinstance(expected_source_state_hash, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_source_state_hash):
            raise DomainError('SOURCE_WRITEBACK_EXPECTED_STATE_INVALID')
        if not isinstance(deleted_by, str) or not 1 <= len(deleted_by) <= 30:
            raise DomainError('SOURCE_DELETE_ACTOR_MAPPING_REQUIRED')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', idempotency_key):
            raise DomainError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID')
        with self.engine.begin() as connection:
            connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                               {'timeout': f'{self.statement_timeout_seconds}s'})
            connection.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                               {'timeout': f'{self.lock_timeout_seconds}s'})
            actor = connection.execute(text(
                f'SELECT id FROM "{self.schema}"."users" WHERE id=:id AND is_active=true'
            ), {'id': deleted_by}).scalar_one_or_none()
            if actor is None:
                raise DomainError('SOURCE_DELETE_ACTOR_MAPPING_REQUIRED')
            row = self._canonical_row(connection, locator.source_record_id, lock=True)
            if row is None:
                raise DomainError('SOURCE_RECORD_NOT_FOUND')
            current_hash = state_hash(self._canonical_state(row))
            # A previously committed delete is an idempotent replay.  The
            # expected hash must still match the pre-delete row for a first try.
            if row.get('deleted_at') is not None or row.get('is_active') is False:
                return {'status': 'APPLIED', 'source_state_hash': current_hash,
                        'idempotent_replay': True}
            if current_hash != expected_source_state_hash:
                raise DomainError('SOURCE_WRITEBACK_CONFLICT')
            assignments = ', '.join([
                f'"{self.columns["is_active"]}"=false',
                f'"{self.columns["deleted_at"]}"=now()',
                f'"{self.columns["deleted_by"]}"=:deleted_by',
                f'"{self.columns["updated_at"]}"=now()',
            ])
            connection.execute(text(
                f'UPDATE "{self.schema}"."{self.table}" SET {assignments} '
                f'WHERE "{self.id_column}"=:source_id'),
                {'source_id': locator.source_record_id, 'deleted_by': deleted_by})
            updated = self._canonical_row(connection, locator.source_record_id, lock=False)
            if updated is None:
                raise DomainError('SOURCE_RECORD_NOT_FOUND')
            return {'status': 'APPLIED', 'source_state_hash': state_hash(self._canonical_state(updated)),
                    'idempotent_replay': False}

    def _ensure_relations(self, connection, resource_id, subject_id, grade_level_id):
        if subject_id is not None:
            valid = connection.execute(text(
                f'SELECT id FROM "{self.schema}"."subjects" '
                'WHERE id=:id AND is_active=true AND deleted_at IS NULL'), {'id': subject_id}).scalar_one_or_none()
            if valid is None:
                raise DomainError('SOURCE_CREATE_SUBJECT_INVALID')
            connection.execute(text(
                f'INSERT INTO "{self.schema}"."resource_subjects" '
                '(resource_id, subject_id, is_primary) VALUES (:resource_id,:subject_id,true) '
                'ON CONFLICT (resource_id, subject_id) DO UPDATE SET is_primary=true'),
                {'resource_id': resource_id, 'subject_id': subject_id})
        if grade_level_id is not None:
            valid = connection.execute(text(
                f'SELECT id FROM "{self.schema}"."grade_levels" '
                'WHERE id=:id AND is_active=true AND deleted_at IS NULL'), {'id': grade_level_id}).scalar_one_or_none()
            if valid is None:
                raise DomainError('SOURCE_CREATE_GRADE_INVALID')
            connection.execute(text(
                f'INSERT INTO "{self.schema}"."resource_grade_levels" '
                '(resource_id, grade_level_id) VALUES (:resource_id,:grade_level_id) '
                'ON CONFLICT (resource_id, grade_level_id) DO NOTHING'),
                {'resource_id': resource_id, 'grade_level_id': grade_level_id})

    def write_metadata(self, *, locator, fields, expected_source_state_hash, idempotency_key):
        if 'update' not in self.actions:
            raise DomainError('SOURCE_WRITEBACK_ACTION_BLOCKED')
        if not self.supports(locator):
            raise DomainError('SOURCE_WRITEBACK_BLOCKED')
        fields = validate_source_writeback_fields(fields)
        if not idempotency_key or len(idempotency_key) > 100:
            raise DomainError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID')
        with self.engine.begin() as connection:
            connection.execute(text("SELECT set_config('statement_timeout', :timeout, true)"),
                               {'timeout': f'{self.statement_timeout_seconds}s'})
            connection.execute(text("SELECT set_config('lock_timeout', :timeout, true)"),
                               {'timeout': f'{self.lock_timeout_seconds}s'})
            row = self._canonical_row(connection, locator.source_record_id, lock=True)
            if row is None:
                raise DomainError('SOURCE_RECORD_NOT_FOUND')
            if (self.idempotency_column is not None
                    and row.get('__writeback_receipt') not in (None, idempotency_key)):
                raise DomainError('SOURCE_WRITEBACK_IDEMPOTENCY_REUSED')
            current = self._canonical_state(row)
            current_hash = state_hash(current)
            if current_hash != expected_source_state_hash:
                raise DomainError('SOURCE_WRITEBACK_CONFLICT')
            assignments = ', '.join(f'"{column}"=:{field}' for field, column in self.columns.items() if field in fields)
            if not assignments:
                raise DomainError('SOURCE_WRITEBACK_FIELD_BLOCKED')
            params = {**fields, 'source_id': locator.source_record_id}
            if self.idempotency_column is not None:
                assignments += f', "{self.idempotency_column}"=:idempotency'
                params['idempotency'] = idempotency_key
            connection.execute(
                text(f'UPDATE "{self.schema}"."{self.table}" SET {assignments} '
                     f'WHERE "{self.id_column}"=:source_id'), params)
            updated = self._canonical_row(connection, locator.source_record_id, lock=False)
            if updated is None:
                raise DomainError('SOURCE_RECORD_NOT_FOUND')
            return {'status': 'APPLIED', 'source_state_hash': state_hash(self._canonical_state(updated))}

    @staticmethod
    def _canonical_state(row):
        return {key: value for key, value in dict(row).items() if key != '__writeback_receipt'}

    def _canonical_row(self, connection, source_id, *, lock):
        """Return the same resource projection used by SOURCE -> MASTER sync."""
        table = f'"{self.schema}"."{self.table}"'
        receipt = (f', r."{self.idempotency_column}" AS __writeback_receipt'
                   if self.idempotency_column is not None else '')
        query = f'''SELECT
            r.id, r.mongo_id, r.provider_id, r.resource_type_id, r.created_by,
            r.title, r.publication_status::text AS publication_status, r.is_active,
            r.deleted_at, r.deleted_by, r.created_at, r.updated_at,
            p.name AS provider_name, p.provider_type::text AS provider_type,
            p.is_active AS provider_is_active, p.deleted_at AS provider_deleted_at,
            p.deleted_by AS provider_deleted_by, p.created_at AS provider_created_at,
            p.updated_at AS provider_updated_at,
            t.code AS resource_type_code, t.name_vi AS resource_type_name_vi,
            t.is_active AS resource_type_is_active, t.deleted_at AS resource_type_deleted_at,
            t.deleted_by AS resource_type_deleted_by, t.created_at AS resource_type_created_at,
            t.updated_at AS resource_type_updated_at{receipt}
        FROM {table} r
        LEFT JOIN "{self.schema}"."providers" p ON p.id = r.provider_id
        JOIN "{self.schema}"."resource_types" t ON t.id = r.resource_type_id
        WHERE r."{self.id_column}"=:source_id'''
        if lock:
            query += ' FOR UPDATE OF r'
        return connection.execute(text(query), {'source_id': source_id}).mappings().one_or_none()
