"""Explicit, permissioned MASTER -> SOURCE metadata write-back workflow."""

import hashlib
import json
import re

from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.domain.source_ownership import (
    SOURCE_WRITE_BACK_FIELDS,
    validate_source_writeback_fields,
)

IDEMPOTENCY_RE = re.compile(r'^[A-Za-z0-9._:-]{8,100}$')
PENDING = 'WRITEBACK_PENDING_APPROVAL'
APPLIED = 'WRITEBACK_APPLIED'
CONFLICT = 'WRITEBACK_CONFLICT'
FAILED = 'WRITEBACK_FAILED'


class SourceWriteBackError(DomainError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


class SourceMetadataWriteBackService:
    """Use a durable MASTER outbox and a separately injected writer capability.

    ``reconciliation_audit_jobs`` is reused only as a durable outbox primitive;
    write-back rows use a distinct status prefix and are never eligible for the
    reconciliation worker's PENDING/RETRY_WAIT queue.
    """

    def __init__(self, uow, writer, settings):
        self.uow, self.writer, self.settings = uow, writer, settings

    def status(self):
        enabled = bool(self.settings.source_metadata_writeback_enabled)
        supported = bool(enabled and getattr(self.writer, 'supported', False))
        return {
            'enabled': enabled,
            'writer_supported': supported,
            'mode': self.settings.source_metadata_writer_mode,
            'fields': sorted(SOURCE_WRITE_BACK_FIELDS),
            'default': 'disabled',
        }

    @staticmethod
    def _fingerprint(resource_id, fields, expected_hash):
        return hashlib.sha256(json.dumps(
            [resource_id, fields, expected_hash], sort_keys=True, separators=(',', ':')
        ).encode()).hexdigest()

    def _authorize(self, resource, actor_id, role, owner_id):
        if role not in ('ADMIN', 'OPERATOR'):
            raise SourceWriteBackError('SOURCE_WRITEBACK_FORBIDDEN', 403)
        if role != 'ADMIN' and (owner_id is None or resource.get('created_by') != owner_id):
            raise SourceWriteBackError('SOURCE_WRITEBACK_OWNER_REQUIRED', 403)
        if (not resource.get('is_active') or resource_deleted(resource)
                or resource.get('source_origin') != 'POSTGRESQL'):
            raise SourceWriteBackError('SOURCE_WRITEBACK_SOURCE_OWNED', 409)
        if not resource.get('source_record_key') or not resource.get('source_state_hash'):
            raise SourceWriteBackError('SOURCE_WRITEBACK_IDENTITY_UNVERIFIED', 409)

    def _validate(self, resource_id, fields, expected_source_state_hash, idempotency_key):
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise SourceWriteBackError('SOURCE_WRITEBACK_RESOURCE_INVALID', 422)
        try:
            fields = validate_source_writeback_fields(fields)
        except DomainError as exc:
            raise SourceWriteBackError(str(exc), 422) from None
        if (not isinstance(expected_source_state_hash, str)
                or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_source_state_hash)):
            raise SourceWriteBackError('SOURCE_WRITEBACK_EXPECTED_STATE_INVALID', 422)
        if not isinstance(idempotency_key, str) or not IDEMPOTENCY_RE.fullmatch(idempotency_key):
            raise SourceWriteBackError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID', 422)
        return fields, expected_source_state_hash.lower()

    def request(self, resource_id, fields, *, expected_source_state_hash, idempotency_key,
                actor_id, role='ADMIN', owner_id=None, approve=False, dry_run=False):
        if not self.settings.source_metadata_writeback_enabled:
            raise SourceWriteBackError('SOURCE_WRITEBACK_DISABLED', 409)
        if not getattr(self.writer, 'supported', False):
            raise SourceWriteBackError('SOURCE_WRITEBACK_UNSUPPORTED', 409)
        fields, expected_source_state_hash = self._validate(
            resource_id, fields, expected_source_state_hash, idempotency_key)
        fingerprint = self._fingerprint(resource_id, fields, expected_source_state_hash)
        job_key = f'SOURCE_WRITEBACK:{idempotency_key}'

        with self.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id)
            if resource is None:
                raise SourceWriteBackError('RESOURCE_NOT_FOUND', 404)
            self._authorize(resource, actor_id, role, owner_id)
            if resource.get('source_state_hash', '').lower() != expected_source_state_hash:
                raise SourceWriteBackError('SOURCE_WRITEBACK_CONFLICT', 409)
            if dry_run:
                return {
                    'request_id': None, 'status': 'DRY_RUN', 'resource_id': resource_id,
                    'fields': sorted(fields), 'writer_supported': True,
                }
            rows = repo.find('reconciliation_audit_jobs', {'job_key': job_key}, limit=1)
            existing = rows[0] if rows else None
            if existing is not None:
                checkpoint = existing.get('checkpoint') or {}
                if checkpoint.get('fingerprint') != fingerprint:
                    raise SourceWriteBackError('SOURCE_WRITEBACK_IDEMPOTENCY_REUSED', 409)
                if existing.get('status') == APPLIED:
                    return self._response(existing)
                if existing.get('status') == CONFLICT:
                    raise SourceWriteBackError('SOURCE_WRITEBACK_CONFLICT', 409)
                request_id = existing['id']
                if not approve:
                    return self._response(existing)
                checkpoint.update(approved_by=actor_id, approved_at=now().isoformat())
                repo.update('reconciliation_audit_jobs', request_id,
                            {'status': 'WRITEBACK_PENDING', 'checkpoint': checkpoint,
                             'finished_at': None, 'error_code': None})
            else:
                request_id = new_id()
                checkpoint = {
                    'kind': 'SOURCE_METADATA_WRITEBACK', 'resource_id': resource_id,
                    'fields': fields, 'expected_source_state_hash': expected_source_state_hash,
                    'fingerprint': fingerprint, 'requested_by': actor_id,
                    'approved_by': actor_id if approve else None,
                    'approved_at': now().isoformat() if approve else None,
                }
                repo.insert('reconciliation_audit_jobs', {
                    'id': request_id, 'job_key': job_key, 'audit_type': 'SOURCE_WRITEBACK',
                    'stream_name': 'resources',
                    'status': 'WRITEBACK_PENDING' if approve else PENDING,
                    'created_at': now(), 'checkpoint': checkpoint,
                })
            source_record_id = resource['source_record_key']

        if not approve:
            return self._response({'id': request_id, 'status': PENDING, 'checkpoint': checkpoint})

        with self.uow() as repo:
            row = repo.get('reconciliation_audit_jobs', request_id)
            attempts = int((row or {}).get('attempts') or 0)
            if attempts >= self.settings.source_metadata_writeback_max_attempts:
                raise SourceWriteBackError('SOURCE_WRITEBACK_RETRY_EXHAUSTED', 409)
            repo.update('reconciliation_audit_jobs', request_id, {
                'status': 'WRITEBACK_PENDING', 'attempts': attempts + 1,
            })
        try:
            result = self.writer.write_metadata(
                record_id=source_record_id, fields=fields,
                expected_source_state_hash=expected_source_state_hash,
                idempotency_key=idempotency_key,
            )
            if not isinstance(result, dict) or result.get('status') != 'APPLIED':
                raise DomainError('SOURCE_WRITEBACK_RESULT_INVALID')
        except DomainError as exc:
            status = CONFLICT if str(exc) == 'SOURCE_WRITEBACK_CONFLICT' else FAILED
            with self.uow() as repo:
                row = repo.get('reconciliation_audit_jobs', request_id)
                checkpoint = {**(row or {}).get('checkpoint', {}), 'error_code': str(exc)}
                repo.update('reconciliation_audit_jobs', request_id,
                            {'status': status, 'checkpoint': checkpoint, 'error_code': str(exc),
                             'finished_at': now()})
            raise SourceWriteBackError(str(exc), 409 if status == CONFLICT else 503) from None
        except Exception:
            with self.uow() as repo:
                row = repo.get('reconciliation_audit_jobs', request_id)
                checkpoint = {**(row or {}).get('checkpoint', {}), 'error_code': 'SOURCE_WRITEBACK_FAILED'}
                repo.update('reconciliation_audit_jobs', request_id,
                            {'status': FAILED if attempts >= self.settings.source_metadata_writeback_max_attempts else 'WRITEBACK_PENDING',
                             'checkpoint': checkpoint,
                             'error_code': 'SOURCE_WRITEBACK_FAILED', 'finished_at': now()})
            raise SourceWriteBackError('SOURCE_WRITEBACK_FAILED', 503) from None
        with self.uow() as repo:
            row = repo.get('reconciliation_audit_jobs', request_id)
            checkpoint = {**(row or {}).get('checkpoint', {}),
                          'applied_at': now().isoformat(),
                          'source_state_hash': result.get('source_state_hash')}
            repo.update('reconciliation_audit_jobs', request_id,
                        {'status': APPLIED, 'checkpoint': checkpoint, 'finished_at': now()})
            return self._response({**(row or {}), 'id': request_id, 'status': APPLIED,
                                   'checkpoint': checkpoint})

    @staticmethod
    def _response(row):
        checkpoint = row.get('checkpoint') or {}
        return {
            'request_id': row.get('id'), 'status': row.get('status'),
            'resource_id': checkpoint.get('resource_id'),
            'fields': sorted((checkpoint.get('fields') or {}).keys()),
            'writer_supported': True,
            'error_code': row.get('error_code'),
        }
