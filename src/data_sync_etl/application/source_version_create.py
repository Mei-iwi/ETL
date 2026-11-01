"""Durable SOURCE CREATE_VERSION orchestration.

The file is uploaded only through the SOURCE object writer.  The original is
never copied into ETL-managed storage; the audit checkpoint stores only the
verified SOURCE locator and checksum.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta

from data_sync_etl.application.source_resource_create import SourceResourceCreateService
from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.source_ids import mapped_source_locator

PENDING = 'SOURCE_VERSION_PENDING'
PROCESSING = 'SOURCE_VERSION_PROCESSING'
APPLIED = 'SOURCE_VERSION_APPLIED'
FAILED = 'SOURCE_VERSION_FAILED'
_KEY = re.compile(r'^[A-Za-z0-9._:-]{8,100}$')


class SourceVersionCreateError(DomainError):
    def __init__(self, code, status=409):
        self.code, self.status = code, status
        super().__init__(code)


class SourceVersionCreateService:
    def __init__(self, uow, version_writer, object_writer, settings):
        self.uow, self.version_writer, self.object_writer, self.settings = (
            uow, version_writer, object_writer, settings)

    def _initial_lifecycle_status(self):
        # SOURCE contracts provide the enum value; fixture writers retain the
        # historical PENDING value used by their in-memory tests.
        return getattr(self.version_writer, 'default_lifecycle_status', 'PENDING')

    def status(self):
        enabled = bool(self.settings.source_metadata_writeback_enabled)
        supported = bool(enabled and getattr(self.version_writer, 'create_supported', False)
                         and getattr(self.object_writer, 'supported', False))
        return {
            'version_create_supported': supported,
            'cloud_upload_supported': supported,
            'version_storage': 'SOURCE_EXTERNAL',
        }

    @staticmethod
    def _validate_file(filename, content_type, file_size):
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
            raise SourceVersionCreateError('SOURCE_VERSION_FILENAME_INVALID', 422)
        if not isinstance(content_type, str) or '/' not in content_type or len(content_type) > 150:
            raise SourceVersionCreateError('SOURCE_VERSION_MIME_INVALID', 422)
        if type(file_size) is not int or file_size < 1:
            raise SourceVersionCreateError('SOURCE_VERSION_FILE_INVALID', 422)

    def request(self, resource_id, *, file_stream, filename, content_type, file_size,
                idempotency_key, actor_id, role=None, expected_sha256=None):
        if not self.settings.source_metadata_writeback_enabled:
            raise SourceVersionCreateError('SOURCE_VERSION_CREATE_DISABLED')
        if not getattr(self.version_writer, 'create_supported', False):
            raise SourceVersionCreateError('SOURCE_VERSION_CONTRACT_UNAVAILABLE')
        if not getattr(self.object_writer, 'supported', False):
            raise SourceVersionCreateError('SOURCE_STORAGE_WRITE_CONTRACT_UNAVAILABLE')
        if role not in ('ADMIN', 'OPERATOR'):
            raise SourceVersionCreateError('SOURCE_VERSION_CREATE_FORBIDDEN', 403)
        if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
            raise SourceVersionCreateError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID', 422)
        if expected_sha256 is not None and (
                not isinstance(expected_sha256, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_sha256)):
            raise SourceVersionCreateError('SOURCE_VERSION_HASH_INVALID', 422)
        self._validate_file(filename, content_type, file_size)
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id, lock=True)
            if resource is None:
                raise SourceVersionCreateError('RESOURCE_NOT_FOUND', 404)
            if resource.get('source_origin') != 'POSTGRESQL' or not resource.get('is_active'):
                raise SourceVersionCreateError('SOURCE_VERSION_SOURCE_OWNERSHIP_REQUIRED')
            try:
                locator = mapped_source_locator(resource)
                source_user_id = SourceResourceCreateService._source_user_id(repo, actor_id)
            except DomainError:
                raise SourceVersionCreateError('SOURCE_VERSION_ACTOR_MAPPING_REQUIRED', 403) from None
            if not self.version_writer.allows_create(actor_id=actor_id, role=role):
                raise SourceVersionCreateError('SOURCE_VERSION_CREATE_FORBIDDEN', 403)
            if not callable(getattr(self.object_writer, 'allows', None)) or not self.object_writer.allows(
                    actor_id=actor_id, role=role):
                raise SourceVersionCreateError('SOURCE_STORAGE_WRITE_FORBIDDEN', 403)
            fingerprint = hashlib.sha256(json.dumps(
                [resource_id, locator.canonical_payload(), filename, content_type.lower(),
                 file_size, expected_sha256.lower() if expected_sha256 else None, actor_id],
                sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            job_key = f'SOURCE_CREATE_VERSION:{idempotency_key}'
            rows = repo.find('reconciliation_audit_jobs', {'job_key': job_key}, limit=1, lock=True)
            if rows:
                row = rows[0]
                if (row.get('checkpoint') or {}).get('fingerprint') != fingerprint:
                    raise SourceVersionCreateError('SOURCE_WRITEBACK_IDEMPOTENCY_REUSED')
                return self._response(row)
            # Upload happens before the durable SOURCE DB call, but the audit
            # checkpoint is written in this same request and includes only the
            # verified object metadata.  A crash before INSERT leaves a private
            # orphan that must be cleaned by the owner-approved storage policy.
            try:
                uploaded = self.object_writer.upload(
                    file_stream, resource_id=locator.source_record_id,
                    idempotency_key=idempotency_key, content_type=content_type,
                    expected_sha256=expected_sha256)
            except DomainError as exc:
                raise SourceVersionCreateError(str(exc), 422) from None
            obj = uploaded.get('object') if isinstance(uploaded, dict) else None
            if obj is None or not obj.sha256 or obj.size != file_size:
                raise SourceVersionCreateError('SOURCE_STORAGE_VERIFICATION_FAILED', 422)
            checkpoint = {
                'kind': 'SOURCE_CREATE_VERSION', 'resource_id': resource_id,
                'source_resource_id': locator.source_record_id, 'source_user_id': source_user_id,
                'source_namespace': locator.namespace,
                'filename': filename, 'content_type': content_type,
                'source_bucket': obj.bucket, 'source_object_key': obj.object_key,
                'source_file_size': obj.size, 'source_hash': obj.sha256.lower(),
                'idempotency_key': idempotency_key, 'requested_by': actor_id,
                'requested_role': role, 'fingerprint': fingerprint,
                'workflow': 'OBJECT_VERIFIED',
            }
            row = repo.insert('reconciliation_audit_jobs', {
                'id': new_id(), 'job_key': job_key, 'audit_type': 'SOURCE_CREATE_VERSION',
                'stream_name': 'resources', 'status': PENDING, 'created_at': now(),
                'checkpoint': checkpoint,
            })
            return self._response(row)

    def apply_for_source_resource(self, *, checkpoint, stager, persist_checkpoint):
        """Apply a version belonging to a newly committed SOURCE resource.

        The normal version endpoint requires an already reconciled Master row.
        CREATE_RESOURCE is the opposite ordering: SOURCE must commit first and
        Master reconciliation follows.  This method reuses the same object and
        version writers while keeping every external boundary checkpointed in
        the parent CREATE job, so a worker crash cannot create a second object
        or version.
        """
        if not self.settings.source_metadata_writeback_enabled:
            raise SourceVersionCreateError('SOURCE_VERSION_CREATE_DISABLED')
        if not getattr(self.version_writer, 'create_supported', False):
            raise SourceVersionCreateError('SOURCE_VERSION_CONTRACT_UNAVAILABLE')
        if not getattr(self.object_writer, 'supported', False):
            raise SourceVersionCreateError('SOURCE_STORAGE_WRITE_CONTRACT_UNAVAILABLE')
        manifest = checkpoint.get('source_file')
        if not isinstance(manifest, dict):
            raise SourceVersionCreateError('SOURCE_VERSION_FILE_MANIFEST_MISSING')
        resource_id = checkpoint.get('source_resource_id')
        source_user_id = checkpoint.get('source_user_id')
        if not isinstance(resource_id, str) or not isinstance(source_user_id, str):
            raise SourceVersionCreateError('SOURCE_VERSION_SOURCE_IDENTITY_MISSING')
        version_key = checkpoint.get('source_version_idempotency_key')
        if not isinstance(version_key, str) or not _KEY.fullmatch(version_key):
            raise SourceVersionCreateError('SOURCE_VERSION_IDEMPOTENCY_INVALID')

        # A previous attempt may have uploaded the object and then crashed
        # before SOURCE version commit.  Verify the checkpointed object before
        # trusting it; if an owner-side cleanup removed it, re-upload the same
        # deterministic key from the staged file instead of creating a
        # dangling resource version.
        if checkpoint.get('source_bucket'):
            verify = getattr(self.object_writer, 'verify', None)
            if callable(verify):
                try:
                    verify(
                        bucket=checkpoint['source_bucket'],
                        object_key=checkpoint['source_object_key'],
                        size=checkpoint['source_file_size'],
                        source_hash=checkpoint['source_hash'],
                    )
                except DomainError as exc:
                    if str(exc) != 'CLOUD_STORAGE_OBJECT_NOT_FOUND':
                        raise SourceVersionCreateError(str(exc), 422) from None
                    for key in ('source_bucket', 'source_object_key', 'source_file_size', 'source_hash'):
                        checkpoint.pop(key, None)
                    persist_checkpoint(checkpoint)

        if not checkpoint.get('source_bucket'):
            try:
                with stager.open(manifest) as stream:
                    uploaded = self.object_writer.upload(
                        stream, resource_id=resource_id,
                        idempotency_key=version_key,
                        content_type=manifest['content_type'],
                        expected_sha256=manifest['source_hash'])
            except DomainError as exc:
                raise SourceVersionCreateError(str(exc), 422) from None
            obj = uploaded.get('object') if isinstance(uploaded, dict) else None
            if (obj is None or obj.size != manifest['file_size'] or not obj.sha256
                    or obj.sha256.lower() != manifest['source_hash'].lower()):
                raise SourceVersionCreateError('SOURCE_STORAGE_VERIFICATION_FAILED', 422)
            checkpoint.update(
                source_bucket=obj.bucket,
                source_object_key=obj.object_key,
                source_file_size=obj.size,
                source_hash=obj.sha256.lower(),
                workflow='OBJECT_VERIFIED',
            )
            persist_checkpoint(checkpoint)

        if not checkpoint.get('source_version_id'):
            reserved_at = now()
            source_version_id = self.version_writer.reserve_version_id(created_at=reserved_at)
            checkpoint.update(source_version_id=source_version_id, version_reserved_at=reserved_at.isoformat())
            persist_checkpoint(checkpoint)

        result = self.version_writer.create_version(
            source_version_id=checkpoint['source_version_id'],
            source_resource_id=resource_id,
            parent_version_id=None,
            version_number=None,
            lifecycle_status=self._initial_lifecycle_status(),
            storage_bucket=checkpoint['source_bucket'],
            storage_object_key=checkpoint['source_object_key'],
            source_hash=checkpoint['source_hash'],
            uploaded_by=source_user_id,
            created_at=datetime.fromisoformat(checkpoint.get('version_reserved_at') or checkpoint['reserved_at']),
            idempotency_key=version_key,
        )
        if not isinstance(result, dict) or result.get('status') != 'APPLIED':
            raise SourceVersionCreateError('SOURCE_VERSION_INVALID_ACKNOWLEDGEMENT')
        checkpoint.update(
            source_version_state_hash=result.get('source_state_hash'),
            workflow='SOURCE_VERSION_COMMITTED',
            source_version_replay=bool(result.get('idempotent_replay')),
        )
        persist_checkpoint(checkpoint)
        stager.remove(manifest)
        return result

    def process_pending_once(self, worker_id='source-version-worker'):
        if not self.settings.source_metadata_writeback_enabled or not getattr(self.version_writer, 'create_supported', False):
            return False
        stamp = now()
        claim = None
        with self.uow() as repo:
            rows = repo.find('reconciliation_audit_jobs', {'audit_type': 'SOURCE_CREATE_VERSION'},
                             order=('created_at',), limit=20, lock=True, skip_locked=True)
            for row in rows:
                expired = row['status'] == PROCESSING and (
                    row.get('lease_expires_at') is None or row['lease_expires_at'] <= stamp)
                if row['status'] != PENDING and not expired:
                    continue
                attempts = int(row.get('attempts') or 0)
                if attempts >= self.settings.source_metadata_writeback_max_attempts:
                    repo.update('reconciliation_audit_jobs', row['id'], {
                        'status': FAILED, 'error_code': 'SOURCE_VERSION_RETRY_EXHAUSTED',
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
            verify = getattr(self.object_writer, 'verify', None)
            if callable(verify):
                verify(bucket=checkpoint['source_bucket'], object_key=checkpoint['source_object_key'],
                       size=checkpoint['source_file_size'], source_hash=checkpoint['source_hash'])
            if not checkpoint.get('source_version_id'):
                reserved_at = now()
                source_version_id = self.version_writer.reserve_version_id(created_at=reserved_at)
                checkpoint.update(source_version_id=source_version_id, reserved_at=reserved_at.isoformat())
                with self.uow() as repo:
                    repo.update('reconciliation_audit_jobs', claim['id'], {'checkpoint': checkpoint})
            result = self.version_writer.create_version(
                source_version_id=checkpoint['source_version_id'],
                source_resource_id=checkpoint['source_resource_id'], parent_version_id=None,
                version_number=None, lifecycle_status=self._initial_lifecycle_status(),
                storage_bucket=checkpoint['source_bucket'],
                storage_object_key=checkpoint['source_object_key'],
                source_hash=checkpoint['source_hash'], uploaded_by=checkpoint['source_user_id'],
                created_at=datetime.fromisoformat(checkpoint['reserved_at']),
                idempotency_key=checkpoint['idempotency_key'])
            if not isinstance(result, dict) or result.get('status') != 'APPLIED':
                raise ValueError('Invalid SOURCE VERSION acknowledgement')
        except SourceVersionCreateError as exc:
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
                    'error_code': 'SOURCE_VERSION_FAILED' if retryable else 'SOURCE_VERSION_RETRY_EXHAUSTED',
                    'finished_at': None if retryable else now(), 'next_retry_at': now() if retryable else None,
                    'claim_token': None, 'lease_expires_at': None,
                })
            return True
        checkpoint.update(applied_at=now().isoformat(), source_version_state_hash=result.get('source_state_hash'),
                          workflow='SOURCE_VERSION_COMMITTED', reconciliation_required=True,
                          idempotent_replay=bool(result.get('idempotent_replay')))
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
            'request_id': row['id'], 'status': row['status'], 'operation': 'CREATE_VERSION',
            'resource_id': checkpoint.get('resource_id'),
            'source_version_id': checkpoint.get('source_version_id'),
            'workflow': checkpoint.get('workflow', 'UPLOAD_PENDING'),
            'reconciliation_required': bool(checkpoint.get('reconciliation_required')),
            'error_code': row.get('error_code'),
        }
