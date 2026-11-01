"""Contract-gated SOURCE soft-delete writeback.

The service deliberately reuses the existing reconciliation audit-job table.
It records intent before the external call and keeps the SOURCE operation
disabled unless the owner contract explicitly includes ``delete``.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from data_sync_etl.application.source_resource_create import SourceResourceCreateService
from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.source_ids import mapped_source_locator

PENDING = 'SOURCE_DELETE_PENDING'
PROCESSING = 'SOURCE_DELETE_PROCESSING'
APPLIED = 'SOURCE_DELETE_APPLIED'
FAILED = 'SOURCE_DELETE_FAILED'
_KEY = re.compile(r'^[A-Za-z0-9._:-]{8,100}$')


class SourceDeleteWriteBackError(DomainError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class SourceResourceDeleteService:
    def __init__(self, uow, writer, settings):
        self.uow, self.writer, self.settings = uow, writer, settings

    def status(self):
        enabled = bool(self.settings.source_metadata_writeback_enabled)
        supported = bool(enabled and getattr(self.writer, 'supported', False)
                         and getattr(self.writer, 'delete_supported', False))
        return {
            'delete_supported': supported,
            'delete_mode': 'SOFT_DELETE_ONLY',
        }

    def request(self, resource_id, *, expected_source_state_hash, idempotency_key,
                actor_id, role=None, approve=False):
        if not self.settings.source_metadata_writeback_enabled:
            raise SourceDeleteWriteBackError('SOURCE_DELETE_DISABLED')
        if not getattr(self.writer, 'supported', False) or not getattr(self.writer, 'delete_supported', False):
            raise SourceDeleteWriteBackError('SOURCE_DELETE_CONTRACT_UNAVAILABLE')
        if role not in ('ADMIN', 'OPERATOR'):
            raise SourceDeleteWriteBackError('SOURCE_DELETE_FORBIDDEN', 403)
        if not isinstance(expected_source_state_hash, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_source_state_hash):
            raise SourceDeleteWriteBackError('SOURCE_WRITEBACK_EXPECTED_STATE_INVALID', 422)
        if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
            raise SourceDeleteWriteBackError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID', 422)
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id, lock=True)
            if resource is None:
                raise SourceDeleteWriteBackError('RESOURCE_NOT_FOUND', 404)
            if resource.get('source_origin') != 'POSTGRESQL':
                raise SourceDeleteWriteBackError('SOURCE_DELETE_SOURCE_OWNERSHIP_REQUIRED')
            if resource.get('source_state_hash') != expected_source_state_hash.lower():
                raise SourceDeleteWriteBackError('SOURCE_WRITEBACK_CONFLICT')
            try:
                locator = mapped_source_locator(resource)
            except DomainError:
                raise SourceDeleteWriteBackError('SOURCE_WRITEBACK_IDENTITY_UNVERIFIED') from None
            try:
                source_user_id = SourceResourceCreateService._source_user_id(repo, actor_id)
            except DomainError:
                raise SourceDeleteWriteBackError('SOURCE_DELETE_ACTOR_MAPPING_REQUIRED', 403) from None
            contract_allowed = bool(self.writer.allows_delete(
                actor_id=actor_id, role=role, source_user_id=source_user_id))
            if not contract_allowed:
                raise SourceDeleteWriteBackError('SOURCE_DELETE_FORBIDDEN', 403)
            fingerprint = hashlib.sha256(json.dumps(
                [resource_id, locator.canonical_payload(), expected_source_state_hash.lower(), actor_id],
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            job_key = f'SOURCE_DELETE_RESOURCE:{idempotency_key}'
            rows = repo.find('reconciliation_audit_jobs', {'job_key': job_key}, limit=1, lock=True)
            row = rows[0] if rows else None
            if row:
                checkpoint = row.get('checkpoint') or {}
                if checkpoint.get('fingerprint') != fingerprint:
                    raise SourceDeleteWriteBackError('SOURCE_WRITEBACK_IDEMPOTENCY_REUSED')
                if row['status'] == APPLIED:
                    return self._response(row)
                if row['status'] == FAILED:
                    raise SourceDeleteWriteBackError(row.get('error_code') or 'SOURCE_DELETE_FAILED')
                if not approve:
                    return self._response(row)
                repo.update('reconciliation_audit_jobs', row['id'], {
                    'status': PENDING,
                    'checkpoint': {**checkpoint, 'approved_by': actor_id,
                                   'approved_role': role, 'approved_at': now().isoformat()},
                })
                return self._response({**row, 'status': PENDING,
                                       'checkpoint': {**checkpoint, 'approved_by': actor_id}})
            if not approve:
                checkpoint = {
                    'kind': 'SOURCE_DELETE_RESOURCE', 'resource_id': resource_id,
                    'expected_source_state_hash': expected_source_state_hash.lower(),
                    'requested_by': actor_id, 'requested_role': role,
                    'source_user_id': source_user_id, 'source_locator': locator.canonical_payload(),
                    'idempotency_key': idempotency_key, 'fingerprint': fingerprint,
                }
                row = repo.insert('reconciliation_audit_jobs', {
                    'id': new_id(), 'job_key': job_key, 'audit_type': 'SOURCE_DELETE',
                    'stream_name': 'resources', 'status': PENDING, 'created_at': now(),
                    'checkpoint': checkpoint,
                })
                return self._response(row)
            raise SourceDeleteWriteBackError('SOURCE_DELETE_APPROVAL_REQUIRED')

    def process_pending_once(self, worker_id='source-delete-worker'):
        if not self.settings.source_metadata_writeback_enabled or not getattr(self.writer, 'delete_supported', False):
            return False
        stamp = now()
        claim = None
        with self.uow() as repo:
            rows = repo.find('reconciliation_audit_jobs', {'audit_type': 'SOURCE_DELETE'},
                             order=('created_at',), limit=20, lock=True, skip_locked=True)
            for row in rows:
                expired = row['status'] == PROCESSING and (
                    row.get('lease_expires_at') is None or row['lease_expires_at'] <= stamp)
                if (row['status'] == PENDING and not (row.get('checkpoint') or {}).get('approved_by')):
                    continue
                if row['status'] != PENDING and not expired:
                    continue
                attempts = int(row.get('attempts') or 0)
                if attempts >= self.settings.source_metadata_writeback_max_attempts:
                    repo.update('reconciliation_audit_jobs', row['id'], {
                        'status': FAILED, 'error_code': 'SOURCE_DELETE_RETRY_EXHAUSTED',
                        'finished_at': stamp, 'claim_token': None, 'lease_expires_at': None,
                    })
                    continue
                token = f'{worker_id}:{row["id"]}'[:36]
                repo.update('reconciliation_audit_jobs', row['id'], {
                    'status': PROCESSING, 'attempts': attempts + 1, 'claim_token': token,
                    'lease_expires_at': stamp + timedelta(seconds=self.settings.reconciliation_lease_seconds),
                    'heartbeat_at': stamp, 'error_code': None,
                })
                claim = {**row, 'attempts': attempts + 1,
                         'checkpoint': dict(row.get('checkpoint') or {})}
                break
        if claim is None:
            return False
        checkpoint = claim['checkpoint']
        try:
            with self.uow() as repo:
                resource = repo.get('master_learning_resources', checkpoint['resource_id'])
                if resource is None or resource.get('source_state_hash') != checkpoint['expected_source_state_hash']:
                    raise SourceDeleteWriteBackError('SOURCE_WRITEBACK_CONFLICT')
                locator = mapped_source_locator(resource)
                try:
                    source_user_id = SourceResourceCreateService._source_user_id(repo, checkpoint['requested_by'])
                except DomainError:
                    raise SourceDeleteWriteBackError('SOURCE_DELETE_ACTOR_MAPPING_REQUIRED', 403) from None
            result = self.writer.delete_resource(
                locator=locator,
                expected_source_state_hash=checkpoint['expected_source_state_hash'],
                deleted_by=source_user_id,
                idempotency_key=checkpoint['idempotency_key'],
            )
            if not isinstance(result, dict) or result.get('status') != 'APPLIED':
                raise ValueError('Invalid SOURCE DELETE acknowledgement')
        except SourceDeleteWriteBackError as exc:
            with self.uow() as repo:
                repo.update('reconciliation_audit_jobs', claim['id'], {
                    'status': FAILED, 'error_code': exc.code, 'finished_at': now(),
                    'claim_token': None, 'lease_expires_at': None,
                })
            return True
        except Exception:
            retryable = claim['attempts'] < self.settings.source_metadata_writeback_max_attempts
            with self.uow() as repo:
                repo.update('reconciliation_audit_jobs', claim['id'], {
                    'status': PENDING if retryable else FAILED,
                    'error_code': 'SOURCE_DELETE_FAILED' if retryable else 'SOURCE_DELETE_RETRY_EXHAUSTED',
                    'finished_at': None if retryable else now(),
                    'next_retry_at': now() if retryable else None,
                    'claim_token': None, 'lease_expires_at': None,
                })
            return True
        checkpoint.update(applied_at=now().isoformat(), source_state_hash=result.get('source_state_hash'),
                          reconciliation_required=True, idempotent_replay=bool(result.get('idempotent_replay')))
        with self.uow() as repo:
            repo.update('reconciliation_audit_jobs', claim['id'], {
                'status': APPLIED, 'checkpoint': checkpoint, 'error_code': None,
                'finished_at': now(), 'next_retry_at': None, 'claim_token': None,
                'lease_expires_at': None,
            })
        return True

    @staticmethod
    def _response(row):
        checkpoint = row.get('checkpoint') or {}
        return {
            'request_id': row['id'], 'status': row['status'], 'operation': 'DELETE_RESOURCE',
            'resource_id': checkpoint.get('resource_id'),
            'reconciliation_required': bool(checkpoint.get('reconciliation_required')),
            'error_code': row.get('error_code'),
        }
