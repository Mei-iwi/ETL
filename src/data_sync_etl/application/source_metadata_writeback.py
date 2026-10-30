"""Explicit SOURCE write-back. The grant resolver and writer are server capabilities."""

import hashlib
import json
import re

from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.search_access import denied
from data_sync_etl.domain.source_ids import mapped_source_locator
from data_sync_etl.domain.source_ownership import (
    SOURCE_WRITE_BACK_FIELDS,
    state_hash,
    validate_source_writeback_fields,
)

PENDING = 'WRITEBACK_PENDING_APPROVAL'
APPLIED = 'WRITEBACK_APPLIED'
CONFLICT = 'WRITEBACK_CONFLICT'
FAILED = 'WRITEBACK_FAILED'
SAFE_ERRORS = frozenset({'SOURCE_WRITEBACK_CONFLICT', 'SOURCE_RECORD_NOT_FOUND',
                        'SOURCE_WRITEBACK_IDEMPOTENCY_REUSED', 'SOURCE_WRITEBACK_BLOCKED'})


class SourceWriteBackError(DomainError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


class SourceMetadataWriteBackService:
    """Two-command intent and bounded explicit retries, isolated from audit workers."""

    def __init__(self, uow, writer, settings, *, permission=None):
        self.uow, self.writer, self.settings = uow, writer, settings
        self.permission = permission

    def status(self):
        enabled = bool(self.settings.source_metadata_writeback_enabled)
        return {'enabled': enabled,
                'writer_supported': bool(enabled and getattr(self.writer, 'supported', False)
                                         and self.permission is not None),
                'mode': self.settings.source_metadata_writer_mode,
                'fields': sorted(SOURCE_WRITE_BACK_FIELDS), 'default': 'disabled'}

    def _authorize(self, resource, actor, role, owner, fields):
        if not actor or role not in ('ADMIN', 'OPERATOR'):
            raise SourceWriteBackError('SOURCE_WRITEBACK_FORBIDDEN', 403)
        if role != 'ADMIN' and (not owner or resource.get('created_by') != owner):
            raise SourceWriteBackError('SOURCE_WRITEBACK_OWNER_REQUIRED', 403)
        if (denied(resource) or resource.get('publication_status') == 'ARCHIVED'
                or resource.get('source_origin') != 'POSTGRESQL'):
            raise SourceWriteBackError('SOURCE_WRITEBACK_BLOCKED', 409)
        try:
            locator = mapped_source_locator(resource)
        except DomainError:
            raise SourceWriteBackError('SOURCE_WRITEBACK_IDENTITY_UNVERIFIED', 409) from None
        if (locator.entity_type != 'resources' or not resource.get('source_state_hash')
                or resource['source_state_hash'] != state_hash(resource)):
            raise SourceWriteBackError('SOURCE_WRITEBACK_IDENTITY_UNVERIFIED', 409)
        try:
            granted = (self.permission is not None and self.permission(
                actor_id=actor, role=role, resource=resource, locator=locator,
                fields=frozenset(fields)) is True)
            supported = self.writer.supports(locator)
        except Exception:
            granted, supported = False, False
        if not granted:
            raise SourceWriteBackError('SOURCE_WRITEBACK_FORBIDDEN', 403)
        if not supported:
            raise SourceWriteBackError('SOURCE_WRITEBACK_UNSUPPORTED', 409)
        return locator

    def request(self, resource_id, fields, *, expected_source_state_hash, idempotency_key,
                actor_id, role=None, owner_id=None, approve=False, dry_run=False):
        if not self.settings.source_metadata_writeback_enabled:
            raise SourceWriteBackError('SOURCE_WRITEBACK_DISABLED', 409)
        if not getattr(self.writer, 'supported', False):
            raise SourceWriteBackError('SOURCE_WRITEBACK_UNSUPPORTED', 409)
        try:
            fields = validate_source_writeback_fields(fields)
        except DomainError as exc:
            raise SourceWriteBackError(str(exc), 422) from None
        if not isinstance(expected_source_state_hash, str) or not re.fullmatch('[a-fA-F0-9]{64}', expected_source_state_hash):
            raise SourceWriteBackError('SOURCE_WRITEBACK_EXPECTED_STATE_INVALID', 422)
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', idempotency_key):
            raise SourceWriteBackError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID', 422)
        # Nonblocking advisory lock across staging and RPC; no MASTER transaction
        # is held during the external call. SQLite is an isolated test fallback.
        with self.uow.processing_lock('source-metadata-writeback') as acquired:
            if not acquired:
                raise SourceWriteBackError('SOURCE_WRITEBACK_BUSY', 409)
            return self._request(resource_id, fields, expected_source_state_hash.lower(), idempotency_key,
                                 actor_id, role, owner_id, approve, dry_run)

    def _request(self, resource_id, fields, expected_hash, key, actor, role, owner, approve, dry_run):
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id, lock=True)
            if resource is None:
                raise SourceWriteBackError('RESOURCE_NOT_FOUND', 404)
            locator = self._authorize(resource, actor, role, owner, fields)
            fingerprint = hashlib.sha256(json.dumps(
                [resource_id, locator.canonical_payload(), fields, expected_hash, actor],
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            rows = repo.find('reconciliation_audit_jobs', {'job_key': f'SOURCE_WRITEBACK:{key}'}, limit=1, lock=True)
            row = rows[0] if rows else None
            cp = dict((row or {}).get('checkpoint') or {})
            if row and cp.get('fingerprint') != fingerprint:
                raise SourceWriteBackError('SOURCE_WRITEBACK_IDEMPOTENCY_REUSED', 409)
            if row and row['status'] == APPLIED:
                return self._response(row)  # Also after canonical SOURCE -> MASTER re-sync.
            if row and row['status'] in (CONFLICT, FAILED):
                raise SourceWriteBackError(cp.get('error_code') or 'SOURCE_WRITEBACK_FAILED', 409)
            if resource['source_state_hash'] != expected_hash:
                raise SourceWriteBackError('SOURCE_WRITEBACK_CONFLICT', 409)
            if dry_run:
                return {'request_id': None, 'status': 'DRY_RUN', 'resource_id': resource_id,
                        'fields': sorted(fields), 'writer_supported': True}
            if row is None:
                if approve:
                    raise SourceWriteBackError('SOURCE_WRITEBACK_APPROVAL_REQUIRED', 409)
                cp = {'kind': 'SOURCE_METADATA_WRITEBACK', 'resource_id': resource_id,
                      'fields': fields, 'expected_source_state_hash': expected_hash,
                      'fingerprint': fingerprint, 'requested_by': actor}
                row = repo.insert('reconciliation_audit_jobs', {
                    'id': new_id(), 'job_key': f'SOURCE_WRITEBACK:{key}', 'audit_type': 'SOURCE_WRITEBACK',
                    'stream_name': 'resources', 'status': PENDING, 'created_at': now(), 'checkpoint': cp})
                return self._response(row)
            if not approve:
                return self._response(row)
            attempts = row['attempts'] + 1
            if attempts > self.settings.source_metadata_writeback_max_attempts:
                raise SourceWriteBackError('SOURCE_WRITEBACK_RETRY_EXHAUSTED', 409)
            cp.update(approved_by=actor, approved_at=now().isoformat())
            repo.update('reconciliation_audit_jobs', row['id'], {
                'status': 'WRITEBACK_PENDING', 'attempts': attempts, 'checkpoint': cp,
                'finished_at': None, 'error_code': None})
        try:
            # Grant revocation and mapping changes must be checked before RPC.
            with self.uow() as repo:
                current = repo.get('master_learning_resources', resource_id)
                if current is None:
                    raise SourceWriteBackError('RESOURCE_NOT_FOUND', 404)
                current_locator = self._authorize(current, actor, role, owner, fields)
                if current_locator != locator or current['source_state_hash'] != expected_hash:
                    raise SourceWriteBackError('SOURCE_WRITEBACK_CONFLICT', 409)
            result = self.writer.write_metadata(locator=locator, fields=fields,
                expected_source_state_hash=expected_hash, idempotency_key=key)
            if (not isinstance(result, dict) or result.get('status') != 'APPLIED'
                    or not re.fullmatch('[0-9a-f]{64}', result.get('source_state_hash') or '')):
                raise ValueError('Invalid writer acknowledgement')
        except Exception as exc:
            code = str(exc) if isinstance(exc, DomainError) and str(exc) in SAFE_ERRORS else 'SOURCE_WRITEBACK_FAILED'
            terminal = code != 'SOURCE_WRITEBACK_FAILED' or attempts >= self.settings.source_metadata_writeback_max_attempts
            status = CONFLICT if code == 'SOURCE_WRITEBACK_CONFLICT' else FAILED if terminal else 'WRITEBACK_PENDING'
            if code == 'SOURCE_WRITEBACK_FAILED' and terminal:
                code = 'SOURCE_WRITEBACK_RETRY_EXHAUSTED'
            cp.update(error_code=code)
            with self.uow() as repo:
                repo.update('reconciliation_audit_jobs', row['id'], {
                    'status': status, 'checkpoint': cp, 'error_code': code,
                    'finished_at': now() if terminal else None})
            if isinstance(exc, SourceWriteBackError):
                raise exc
            raise SourceWriteBackError(code, 409 if status == CONFLICT else 503) from None
        cp.pop('error_code', None)
        cp.update(applied_at=now().isoformat(), source_state_hash=result['source_state_hash'],
                  reconciliation_required=True)
        with self.uow() as repo:
            repo.update('reconciliation_audit_jobs', row['id'], {
                'status': APPLIED, 'checkpoint': cp, 'error_code': None, 'finished_at': now()})
        return self._response({**row, 'status': APPLIED, 'checkpoint': cp, 'error_code': None})

    @staticmethod
    def _response(row):
        cp = row.get('checkpoint') or {}
        return {'request_id': row['id'], 'status': row['status'],
                'resource_id': cp.get('resource_id'), 'fields': sorted(cp.get('fields') or {}),
                'writer_supported': True, 'error_code': row.get('error_code')}
