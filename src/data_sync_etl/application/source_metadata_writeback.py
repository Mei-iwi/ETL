"""Explicit SOURCE write-back. The grant resolver and writer are server capabilities."""

import hashlib
import json
import re
from datetime import timedelta

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
        grant_ready = bool(getattr(self.writer, 'grant_configured', True))
        contract_fields = set(getattr(self.writer, 'update_columns', None)
                              or getattr(self.writer, 'columns', None) or {'title'})
        return {'enabled': enabled,
                'writer_supported': bool(enabled and getattr(self.writer, 'supported', False)
                                         and self.permission is not None and grant_ready),
                'mode': self.settings.source_metadata_writer_mode,
                'fields': sorted(contract_fields & set(SOURCE_WRITE_BACK_FIELDS)),
                'auto_approval': bool(getattr(self.settings, 'source_write_auto_approval_enabled', False)),
                'default': 'disabled'}

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
                actor_id, role=None, owner_id=None, approve=False, dry_run=False,
                auto_approve=False):
        # This switch is fail-closed: callers cannot request policy approval
        # unless deployment explicitly enables it.  Contract/actor/field
        # authorization is still performed by _authorize before a job is
        # persisted, so the flag never widens SOURCE permissions.
        if auto_approve and not getattr(self.settings, 'source_write_auto_approval_enabled', False):
            raise SourceWriteBackError('SOURCE_WRITEBACK_APPROVAL_REQUIRED', 409)
        if not self.settings.source_metadata_writeback_enabled:
            raise SourceWriteBackError('SOURCE_WRITEBACK_DISABLED', 409)
        if not getattr(self.writer, 'supported', False):
            raise SourceWriteBackError('SOURCE_WRITEBACK_UNSUPPORTED', 409)
        try:
            allowed_fields = (getattr(self.writer, 'update_columns', None)
                              or getattr(self.writer, 'columns', None) or frozenset({'title'}))
            fields = validate_source_writeback_fields(fields, allowed_fields=allowed_fields)
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
                                 actor_id, role, owner_id, approve, dry_run, auto_approve)

    def process_pending_once(self, worker_id='source-writeback-worker'):
        """Apply one approved, durable write-back job.

        The API still supports an immediate approved call for backwards
        compatibility.  This path is the restart-safe recovery worker for jobs
        left queued when the API process stopped before the external SOURCE RPC
        completed.  It uses the existing audit-job table; no new schema is
        introduced and disabled/unsupported writers remain idle.
        """
        if not self.settings.source_metadata_writeback_enabled or not getattr(self.writer, 'supported', False):
            return False
        stamp = now()
        claim = None
        with self.uow() as repo:
            candidates = repo.find('reconciliation_audit_jobs', {'audit_type': 'SOURCE_WRITEBACK'},
                                   order=('created_at',), limit=20, lock=True, skip_locked=True)
            for row in candidates:
                status = row.get('status')
                due = row.get('next_retry_at') is None or row['next_retry_at'] <= stamp
                lease_expired = status == 'WRITEBACK_PROCESSING' and (
                    row.get('lease_expires_at') is None or row['lease_expires_at'] <= stamp)
                # A plain pending request is not executable.  It becomes
                # worker-visible only after an explicit approval or a
                # contract/setting-gated pre-authorized policy marker.
                if status in (PENDING, 'WRITEBACK_PENDING') and not (row.get('checkpoint') or {}).get('approved_by'):
                    continue
                if not ((status in (PENDING, 'WRITEBACK_PENDING') and due) or lease_expired):
                    continue
                attempts = int(row.get('attempts') or 0)
                if attempts >= self.settings.source_metadata_writeback_max_attempts:
                    repo.update('reconciliation_audit_jobs', row['id'], {
                        'status': FAILED, 'error_code': 'SOURCE_WRITEBACK_RETRY_EXHAUSTED',
                        'finished_at': stamp, 'claim_token': None, 'lease_expires_at': None,
                    })
                    continue
                token = f'{worker_id}:{row["id"]}'[:36]
                repo.update('reconciliation_audit_jobs', row['id'], {
                    'status': 'WRITEBACK_PROCESSING', 'attempts': attempts + 1,
                    'claim_token': token,
                    'lease_expires_at': stamp + timedelta(seconds=self.settings.reconciliation_lease_seconds),
                    'heartbeat_at': stamp, 'error_code': None,
                })
                claim = {**row, 'claim_token': token, 'attempts': attempts + 1,
                         'checkpoint': dict(row.get('checkpoint') or {})}
                break
        if claim is None:
            return False

        checkpoint = claim['checkpoint']
        resource_id = checkpoint.get('resource_id')
        fields = checkpoint.get('fields') or {}
        expected_hash = checkpoint.get('expected_source_state_hash')
        actor = checkpoint.get('approved_by') or checkpoint.get('requested_by')
        role = checkpoint.get('approved_role') or checkpoint.get('requested_role') or 'ADMIN'
        owner = checkpoint.get('owner_id')
        key = checkpoint.get('idempotency_key') or claim['job_key'].removeprefix('SOURCE_WRITEBACK:')
        try:
            with self.uow() as repo:
                resource = repo.get('master_learning_resources', resource_id)
                if resource is None:
                    raise SourceWriteBackError('RESOURCE_NOT_FOUND', 404)
                locator = self._authorize(resource, actor, role, owner, fields)
                if resource.get('source_state_hash') != expected_hash:
                    raise SourceWriteBackError('SOURCE_WRITEBACK_CONFLICT', 409)
            result = self.writer.write_metadata(
                locator=locator, fields=fields,
                expected_source_state_hash=expected_hash, idempotency_key=key,
            )
            if (not isinstance(result, dict) or result.get('status') != 'APPLIED'
                    or not re.fullmatch('[0-9a-f]{64}', result.get('source_state_hash') or '')):
                raise ValueError('Invalid writer acknowledgement')
        except SourceWriteBackError as exc:
            with self.uow() as repo:
                repo.update('reconciliation_audit_jobs', claim['id'], {
                    'status': CONFLICT if exc.code == 'SOURCE_WRITEBACK_CONFLICT' else FAILED,
                    'error_code': exc.code, 'finished_at': now(),
                    'claim_token': None, 'lease_expires_at': None,
                })
            return True
        except Exception:
            retryable = claim['attempts'] < self.settings.source_metadata_writeback_max_attempts
            with self.uow() as repo:
                repo.update('reconciliation_audit_jobs', claim['id'], {
                    'status': 'WRITEBACK_PENDING' if retryable else FAILED,
                    'error_code': 'SOURCE_WRITEBACK_FAILED' if retryable else 'SOURCE_WRITEBACK_RETRY_EXHAUSTED',
                    'finished_at': None if retryable else now(),
                    'next_retry_at': now() + timedelta(seconds=self.settings.reconciliation_retry_seconds)
                    if retryable else None,
                    'claim_token': None, 'lease_expires_at': None,
                })
            return True
        checkpoint.pop('error_code', None)
        checkpoint.update(applied_at=now().isoformat(), source_state_hash=result['source_state_hash'],
                          reconciliation_required=True)
        with self.uow() as repo:
            repo.update('reconciliation_audit_jobs', claim['id'], {
                'status': APPLIED, 'checkpoint': checkpoint, 'error_code': None,
                'finished_at': now(), 'next_retry_at': None,
                'claim_token': None, 'lease_expires_at': None,
            })
        return True

    def _request(self, resource_id, fields, expected_hash, key, actor, role, owner, approve, dry_run,
                 auto_approve):
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
            if row and row['status'] == 'WRITEBACK_PROCESSING' and row.get('lease_expires_at') and row['lease_expires_at'] > now():
                raise SourceWriteBackError('SOURCE_WRITEBACK_BUSY', 409)
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
                if approve and not auto_approve:
                    raise SourceWriteBackError('SOURCE_WRITEBACK_APPROVAL_REQUIRED', 409)
                cp = {'kind': 'SOURCE_METADATA_WRITEBACK', 'resource_id': resource_id,
                      'fields': fields, 'expected_source_state_hash': expected_hash,
                      'fingerprint': fingerprint, 'requested_by': actor,
                      'requested_role': role, 'owner_id': owner, 'idempotency_key': key}
                if auto_approve:
                    cp.update(approved_by=actor, approved_role=role,
                              approved_by_policy=True,
                              policy='SOURCE_PREAUTHORIZED_CONTRACT',
                              approved_at=now().isoformat())
                row = repo.insert('reconciliation_audit_jobs', {
                    'id': new_id(), 'job_key': f'SOURCE_WRITEBACK:{key}', 'audit_type': 'SOURCE_WRITEBACK',
                    'stream_name': 'resources', 'status': PENDING, 'created_at': now(), 'checkpoint': cp})
                return self._response(row)
            if not approve and not auto_approve:
                return self._response(row)
            attempts = row['attempts'] + 1
            if attempts > self.settings.source_metadata_writeback_max_attempts:
                raise SourceWriteBackError('SOURCE_WRITEBACK_RETRY_EXHAUSTED', 409)
            lease_started = now()
            claim_token = f'api:{actor}:{key}'[:36]
            cp.update(approved_by=actor, approved_role=role,
                      approved_by_policy=bool(auto_approve),
                      policy='SOURCE_PREAUTHORIZED_CONTRACT' if auto_approve else 'EXPLICIT_APPROVAL',
                      approved_at=now().isoformat())
            repo.update('reconciliation_audit_jobs', row['id'], {
                'status': 'WRITEBACK_PROCESSING', 'attempts': attempts, 'checkpoint': cp,
                'finished_at': None, 'error_code': None,
                'claim_token': claim_token,
                'lease_expires_at': lease_started + timedelta(seconds=self.settings.reconciliation_lease_seconds),
                'heartbeat_at': lease_started})
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
                    'finished_at': now() if terminal else None,
                    'next_retry_at': None if terminal else now() + timedelta(seconds=self.settings.reconciliation_retry_seconds),
                    'claim_token': None, 'lease_expires_at': None})
            if isinstance(exc, SourceWriteBackError):
                raise exc
            raise SourceWriteBackError(code, 409 if status == CONFLICT else 503) from None
        cp.pop('error_code', None)
        cp.update(applied_at=now().isoformat(), source_state_hash=result['source_state_hash'],
                  reconciliation_required=True)
        with self.uow() as repo:
            repo.update('reconciliation_audit_jobs', row['id'], {
                'status': APPLIED, 'checkpoint': cp, 'error_code': None, 'finished_at': now(),
                'next_retry_at': None, 'claim_token': None, 'lease_expires_at': None})
        return self._response({**row, 'status': APPLIED, 'checkpoint': cp, 'error_code': None})

    @staticmethod
    def _response(row):
        cp = row.get('checkpoint') or {}
        return {'request_id': row['id'], 'status': row['status'],
                'resource_id': cp.get('resource_id'), 'fields': sorted(cp.get('fields') or {}),
                'writer_supported': True, 'error_code': row.get('error_code')}
