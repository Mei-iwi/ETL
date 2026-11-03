"""Fail-closed metadata-only CREATE write-back from MASTER to PostgreSQL SOURCE.

SOURCE owns the canonical resource row.  MASTER stores only the durable audit
intent/checkpoint until the existing SOURCE -> MASTER sync observes the commit.
The application never creates a SOURCE user and never accepts a caller-supplied
SOURCE identity without validating the existing MASTER -> SOURCE mapping.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.source_ids import SourceIdentity, SourceLocator

PENDING = 'SOURCE_CREATE_PENDING'
PROCESSING = 'SOURCE_CREATE_PROCESSING'
APPLIED = 'SOURCE_CREATE_APPLIED'
FAILED = 'SOURCE_CREATE_FAILED'


class SourceCreateWriteBackError(DomainError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


_KEY = re.compile(r'^[A-Za-z0-9._:-]{8,100}$')


class SourceResourceCreateService:
    """Queue and recover SOURCE metadata CREATE jobs without schema changes."""

    def __init__(self, uow, writer, settings, *, version_service=None, stager=None):
        self.uow, self.writer, self.settings = uow, writer, settings
        self.version_service, self.stager = version_service, stager
        self.last_source_committed = False
        self.last_request_id = None

    def status(self):
        create_supported = bool(
            self.settings.source_metadata_writeback_enabled
            and getattr(self.writer, 'supported', False)
            and getattr(self.writer, 'create_supported', False)
        )
        version_status = self.version_service.status() if self.version_service is not None else {}
        return {
            'enabled': bool(self.settings.source_metadata_writeback_enabled),
            'create_supported': create_supported,
            'mode': self.settings.source_metadata_writer_mode,
            'operation': 'CREATE_RESOURCE',
            'storage': 'METADATA_ONLY',
            'actor_mapping': 'MASTER_USER_SOURCE_ID_REQUIRED',
            'version_create_supported': bool(version_status.get('version_create_supported')),
        }

    @staticmethod
    def _source_user_id(repo, actor_id, *, user=None):
        if not isinstance(actor_id, str) or not actor_id.startswith('master:'):
            raise SourceCreateWriteBackError('SOURCE_CREATE_ACTOR_MAPPING_REQUIRED', 403)
        master_id = actor_id.removeprefix('master:')
        user = repo.get('master_users', master_id) if user is None else user
        if user and user.get('id') != master_id:
            raise SourceCreateWriteBackError('SOURCE_CREATE_ACTOR_MAPPING_REQUIRED', 403)
        if not user or user.get('source_origin') != 'POSTGRESQL' or not user.get('is_active'):
            raise SourceCreateWriteBackError('SOURCE_CREATE_ACTOR_MAPPING_REQUIRED', 403)
        if user.get('deleted_at') is not None or user.get('source_missing_at') is not None:
            raise SourceCreateWriteBackError('SOURCE_CREATE_ACTOR_MAPPING_REQUIRED', 403)
        try:
            locator = SourceLocator(
                version=user['source_identity_version'], backend=user['source_backend'],
                namespace=user['source_namespace'], entity_type=user['source_entity_type'],
                source_record_id=user['source_record_key'],
            )
            SourceIdentity(locator=locator, digest=user['source_identity_hash'])
        except (KeyError, TypeError, ValueError):
            raise SourceCreateWriteBackError('SOURCE_CREATE_ACTOR_MAPPING_REQUIRED', 403) from None
        if locator.entity_type.casefold() != 'users' or locator.backend.casefold() != 'postgresql':
            raise SourceCreateWriteBackError('SOURCE_CREATE_ACTOR_MAPPING_REQUIRED', 403)
        return locator.source_record_id

    @staticmethod
    def _validate_payload(payload):
        required = ('provider_id', 'resource_type_id', 'title', 'publication_status')
        if any(not isinstance(payload.get(key), str) or not payload[key].strip() for key in required):
            raise SourceCreateWriteBackError('SOURCE_CREATE_METADATA_INVALID', 422)
        if len(payload['title'].strip()) > 500:
            raise SourceCreateWriteBackError('SOURCE_CREATE_TITLE_INVALID', 422)
        if payload['publication_status'] not in ('DRAFT', 'PUBLISHED', 'ARCHIVED'):
            raise SourceCreateWriteBackError('SOURCE_CREATE_PUBLICATION_STATUS_INVALID', 422)
        for key in ('provider_id', 'resource_type_id', 'subject_id', 'grade_level_id'):
            if payload.get(key) is not None and (not isinstance(payload[key], str) or len(payload[key]) > 30):
                raise SourceCreateWriteBackError('SOURCE_CREATE_METADATA_INVALID', 422)
        return {
            'provider_id': payload['provider_id'].strip(),
            'resource_type_id': payload['resource_type_id'].strip(),
            'title': payload['title'].strip(),
            'publication_status': payload['publication_status'],
            'subject_id': (payload.get('subject_id') or '').strip() or None,
            'grade_level_id': (payload.get('grade_level_id') or '').strip() or None,
        }

    @staticmethod
    def _fingerprint(payload, actor_id, source_user_id, source_file=None):
        return hashlib.sha256(json.dumps(
            [payload, actor_id, source_user_id, source_file], sort_keys=True, separators=(',', ':')
        ).encode()).hexdigest()

    def request(self, payload, *, idempotency_key, actor_id, role=None, source_file=None):
        if not self.settings.source_metadata_writeback_enabled:
            raise SourceCreateWriteBackError('SOURCE_CREATE_DISABLED', 409)
        if not getattr(self.writer, 'supported', False) or not getattr(self.writer, 'create_supported', False):
            raise SourceCreateWriteBackError('SOURCE_CREATE_CONTRACT_UNAVAILABLE', 409)
        if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
            raise SourceCreateWriteBackError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID', 422)
        if role not in ('ADMIN', 'OPERATOR'):
            raise SourceCreateWriteBackError('SOURCE_CREATE_FORBIDDEN', 403)
        payload = self._validate_payload(payload)
        with self.uow() as repo:
            source_user_id = self._source_user_id(repo, actor_id)
            if not self.writer.allows_create(
                    actor_id=actor_id, role=role, source_user_id=source_user_id):
                raise SourceCreateWriteBackError('SOURCE_CREATE_FORBIDDEN', 403)
            if source_file is not None:
                if self.version_service is None or self.stager is None:
                    raise SourceCreateWriteBackError('SOURCE_CREATE_FILE_WORKFLOW_UNAVAILABLE', 409)
                if not isinstance(source_file, dict) or not all(
                        isinstance(source_file.get(key), (str, int))
                        for key in ('path', 'filename', 'content_type', 'file_size', 'source_hash')):
                    raise SourceCreateWriteBackError('SOURCE_CREATE_FILE_MANIFEST_INVALID', 422)
            fingerprint = self._fingerprint(payload, actor_id, source_user_id, source_file)
            job_key = f'SOURCE_CREATE_RESOURCE:{idempotency_key}'
            rows = repo.find('reconciliation_audit_jobs', {'job_key': job_key}, limit=1, lock=True)
            row = rows[0] if rows else None
            if row:
                checkpoint = row.get('checkpoint') or {}
                if checkpoint.get('fingerprint') != fingerprint:
                    raise SourceCreateWriteBackError('SOURCE_CREATE_IDEMPOTENCY_REUSED', 409)
                if row['status'] == APPLIED:
                    return self._response(row)
                if row['status'] == FAILED:
                    # An explicit resubmission retries the same durable intent,
                    # IDs and verified object, rather than creating a duplicate.
                    checkpoint = dict(checkpoint)
                    checkpoint.pop('error_code', None)
                    repo.update('reconciliation_audit_jobs', row['id'], {
                        'status': PENDING, 'attempts': 0, 'finished_at': None,
                        'next_retry_at': None, 'error_code': None,
                        'claim_token': None, 'lease_expires_at': None,
                        'checkpoint': checkpoint,
                    })
                    return self._response({**row, 'status': PENDING, 'error_code': None,
                                           'checkpoint': checkpoint})
                return self._response(row)
            checkpoint = {
                'kind': 'SOURCE_CREATE_RESOURCE', 'payload': payload,
                'requested_by': actor_id, 'requested_role': role,
                'source_user_id': source_user_id, 'idempotency_key': idempotency_key,
                'fingerprint': fingerprint,
                'source_namespace': getattr(self.writer, 'namespace', 'postgresql:learning'),
            }
            if source_file is not None:
                checkpoint.update(
                    source_file=source_file,
                    source_version_idempotency_key=(
                        'srcv-' + hashlib.sha256(idempotency_key.encode()).hexdigest()[:48]),
                    workflow='SOURCE_RESOURCE_PENDING',
                )
            row = repo.insert('reconciliation_audit_jobs', {
                'id': new_id(), 'job_key': job_key, 'audit_type': 'SOURCE_CREATE',
                'stream_name': 'resources', 'status': PENDING, 'created_at': now(),
                'checkpoint': checkpoint,
            })
            return self._response(row)

    def process_pending_once(self, worker_id='source-create-worker', *, request_id=None):
        self.last_source_committed = False
        if not self.settings.source_metadata_writeback_enabled or not getattr(self.writer, 'create_supported', False):
            return False
        stamp = now()
        claim = None
        with self.uow() as repo:
            filters = {'audit_type': 'SOURCE_CREATE'}
            if request_id is not None:
                filters['id'] = request_id
            candidates = repo.find('reconciliation_audit_jobs', filters,
                                   order=('created_at',), limit=20, lock=True, skip_locked=True)
            for row in candidates:
                due = row.get('next_retry_at') is None or row['next_retry_at'] <= stamp
                expired = row['status'] == PROCESSING and (
                    row.get('lease_expires_at') is None or row['lease_expires_at'] <= stamp)
                if not ((row['status'] == PENDING and due) or expired):
                    continue
                attempts = int(row.get('attempts') or 0)
                if attempts >= self.settings.source_metadata_writeback_max_attempts:
                    repo.update('reconciliation_audit_jobs', row['id'], {
                        'status': FAILED, 'error_code': 'SOURCE_CREATE_RETRY_EXHAUSTED',
                        'finished_at': stamp, 'claim_token': None, 'lease_expires_at': None,
                    })
                    continue
                token = f'{worker_id}:{row["id"]}'[:36]
                repo.update('reconciliation_audit_jobs', row['id'], {
                    'status': PROCESSING, 'attempts': attempts + 1,
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
        self.last_request_id = claim['id']
        try:
            payload = dict(checkpoint['payload'])
            actor_id = checkpoint['requested_by']
            source_user_id = checkpoint['source_user_id']
            with self.uow() as repo:
                current_source_user = self._source_user_id(repo, actor_id)
                if current_source_user != source_user_id:
                    raise SourceCreateWriteBackError('SOURCE_CREATE_ACTOR_MAPPING_CHANGED', 409)
            if not checkpoint.get('source_resource_id'):
                created_at = now()
                source_id = self.writer.reserve_resource_id(created_at=created_at)
                checkpoint.update(source_resource_id=source_id, reserved_at=created_at.isoformat())
                # Persist the id before the SOURCE transaction.  A retry after
                # an uncertain commit now targets the same canonical id.
                with self.uow() as repo:
                    repo.update('reconciliation_audit_jobs', claim['id'], {'checkpoint': checkpoint})
            result = self.writer.create_resource(
                source_resource_id=checkpoint['source_resource_id'], source_user_id=source_user_id,
                provider_id=payload['provider_id'], resource_type_id=payload['resource_type_id'],
                title=payload['title'], publication_status=payload['publication_status'],
                subject_id=payload.get('subject_id'), grade_level_id=payload.get('grade_level_id'),
                created_at=datetime.fromisoformat(checkpoint['reserved_at']),
                idempotency_key=checkpoint['idempotency_key'],
            )
            if (not isinstance(result, dict) or result.get('status') != 'APPLIED'
                    or result.get('source_resource_id') != checkpoint['source_resource_id']):
                raise ValueError('Invalid SOURCE CREATE acknowledgement')
            checkpoint.update(resource_committed_at=now().isoformat(),
                              source_state_hash=result.get('source_state_hash'))
            with self.uow() as repo:
                repo.update('reconciliation_audit_jobs', claim['id'], {'checkpoint': dict(checkpoint)})
            if checkpoint.get('source_file'):
                if self.version_service is None or self.stager is None:
                    raise SourceCreateWriteBackError('SOURCE_CREATE_FILE_WORKFLOW_UNAVAILABLE', 409)

                def persist(updated):
                    with self.uow() as repo:
                        repo.update('reconciliation_audit_jobs', claim['id'], {'checkpoint': dict(updated)})

                self.version_service.apply_for_source_resource(
                    checkpoint=checkpoint, stager=self.stager, persist_checkpoint=persist)
            # Expose the commit to the worker only after any requested file
            # version has also reached SOURCE.  A resource row alone must not
            # be reported as fully reconciled when the version/storage leg is
            # still retrying.
            self.last_source_committed = True
        except SourceCreateWriteBackError as exc:
            self._finish_error(claim, exc.code, terminal=True, checkpoint=checkpoint)
            return True
        except Exception as exc:
            candidate = str(exc)
            code = (candidate if isinstance(exc, DomainError)
                    and re.fullmatch(r'[A-Z][A-Z0-9_]{2,80}', candidate)
                    else 'SOURCE_CREATE_FAILED')
            original = getattr(exc, 'orig', None)
            sqlstate = getattr(original, 'sqlstate', None)
            if isinstance(sqlstate, str) and re.fullmatch('[A-Z0-9]{5}', sqlstate):
                checkpoint['failure_sqlstate'] = sqlstate
                code = {
                    '42501': 'SOURCE_CREATE_PERMISSION_DENIED',
                    '23503': 'SOURCE_CREATE_REFERENCE_INVALID',
                    '23505': 'SOURCE_CREATE_IDENTITY_CONFLICT',
                    '22P02': 'SOURCE_CREATE_VALUE_INVALID',
                }.get(sqlstate, 'SOURCE_CREATE_DATABASE_FAILED')
            retryable = claim['attempts'] < self.settings.source_metadata_writeback_max_attempts
            self._finish_error(claim, code, terminal=not retryable, checkpoint=checkpoint)
            return True
        checkpoint.update(
            applied_at=now().isoformat(), source_state_hash=result.get('source_state_hash'),
            idempotent_replay=bool(result.get('idempotent_replay')), reconciliation_required=True,
        )
        checkpoint.pop('error_code', None)
        with self.uow() as repo:
            repo.update('reconciliation_audit_jobs', claim['id'], {
                'status': APPLIED, 'checkpoint': checkpoint, 'error_code': None,
                'finished_at': now(), 'next_retry_at': None,
                'claim_token': None, 'lease_expires_at': None,
            })
        return True

    def _finish_error(self, claim, code, *, terminal, checkpoint=None):
        checkpoint = checkpoint or claim['checkpoint']
        with self.uow() as repo:
            repo.update('reconciliation_audit_jobs', claim['id'], {
                'status': FAILED if terminal else PENDING, 'error_code': code,
                'finished_at': now() if terminal else None,
                'next_retry_at': None if terminal else now(),
                'claim_token': None, 'lease_expires_at': None,
                'checkpoint': {**checkpoint, 'error_code': code},
            })

    @staticmethod
    def _response(row):
        checkpoint = row.get('checkpoint') or {}
        return {
            'request_id': row['id'], 'status': row['status'],
            'operation': 'CREATE_RESOURCE',
            'source_resource_id': checkpoint.get('source_resource_id'),
            'source_version_id': checkpoint.get('source_version_id'),
            'master_resource_id': checkpoint.get('master_resource_id'),
            'master_version_id': checkpoint.get('master_version_id'),
            'workflow': checkpoint.get('workflow', 'SOURCE_RESOURCE_PENDING'),
            'source_commit_status': ('SOURCE_COMMITTED' if checkpoint.get('resource_committed_at')
                                     or checkpoint.get('workflow') in {'OBJECT_VERIFIED', 'SOURCE_VERSION_COMMITTED'}
                                     else 'PENDING'),
            'master_reconciliation_status': (
                'MASTER_RECONCILED' if checkpoint.get('master_resource_id') else
                'PENDING_RECONCILIATION' if checkpoint.get('reconciliation_required') else 'NOT_STARTED'),
            'reconciliation_required': bool(checkpoint.get('reconciliation_required')),
            'error_code': row.get('error_code'),
        }

    def status_for_request(self, request_id):
        with self.uow() as repo:
            rows = repo.find('reconciliation_audit_jobs', {'id': request_id}, limit=1)
            if not rows:
                return None
            result = self._response(rows[0])
            if result.get('source_resource_id') and not result.get('master_resource_id'):
                matches = repo.find(
                    'master_learning_resources',
                    {'source_record_key': result['source_resource_id']},
                    limit=2,
                )
                if len(matches) == 1:
                    result['master_resource_id'] = matches[0].get('id')
            if result.get('master_resource_id') and result.get('source_version_id'):
                mappings = repo.find('source_file_imports', {
                    'resource_id': result['master_resource_id'],
                    'source_version_id': result['source_version_id'],
                }, limit=2)
                if len(mappings) == 1:
                    result['master_version_id'] = mappings[0].get('target_version_id')
                result['master_reconciliation_status'] = (
                    'MASTER_RECONCILED' if result.get('master_version_id') else 'PENDING_RECONCILIATION')
            return result

    def mark_master_reconciled(self):
        """Attach the canonical Master id after the normal SOURCE sync commits."""
        if not self.last_request_id:
            return
        with self.uow() as repo:
            rows = repo.find('reconciliation_audit_jobs', {'id': self.last_request_id}, limit=1, lock=True)
            if not rows:
                return
            row = rows[0]
            checkpoint = dict(row.get('checkpoint') or {})
            source_id = checkpoint.get('source_resource_id')
            if not source_id:
                return
            matches = repo.find('master_learning_resources', {'source_record_key': source_id}, limit=2)
            if len(matches) != 1:
                return
            checkpoint['master_resource_id'] = matches[0].get('id')
            if checkpoint.get('source_version_id'):
                mappings = repo.find('source_file_imports', {
                    'resource_id': matches[0]['id'],
                    'source_version_id': checkpoint['source_version_id'],
                }, limit=2)
                if len(mappings) != 1 or not mappings[0].get('target_version_id'):
                    return
                checkpoint['master_version_id'] = mappings[0]['target_version_id']
            checkpoint['master_reconciliation_status'] = 'MASTER_RECONCILED'
            repo.update('reconciliation_audit_jobs', row['id'], {'checkpoint': checkpoint})
