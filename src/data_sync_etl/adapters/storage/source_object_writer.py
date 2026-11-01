"""Explicit SOURCE-owned object writer.

The reader and ETL-managed destination adapters remain separate.  This writer
is constructed only from a SOURCE owner contract and a separate credential;
without both it is unavailable and cannot be selected by a caller.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from data_sync_etl.domain.core import DomainError, UploadTooLargeError
from data_sync_etl.ports.cloud_storage import CloudStoragePort


class DisabledSourceObjectStorageWriter:
    supported = False

    def upload(self, **kwargs):
        raise DomainError('SOURCE_STORAGE_WRITE_DISABLED')


class SourceObjectStorageWriter:
    supported = True

    def __init__(self, cloud: CloudStoragePort, contract_file: Path):
        try:
            contract = json.loads(Path(contract_file).read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise DomainError('SOURCE_STORAGE_WRITE_CONTRACT_UNAVAILABLE') from exc
        if not isinstance(contract, dict) or contract.get('backend') != 's3-compatible':
            raise DomainError('SOURCE_STORAGE_WRITE_CONTRACT_MISMATCH')
        if contract.get('provider_id') != cloud.provider_id or contract.get('bucket') != cloud.bucket:
            raise DomainError('SOURCE_STORAGE_WRITE_CONTRACT_MISMATCH')
        actions = contract.get('actions', [])
        if not isinstance(actions, list) or actions != ['upload']:
            raise DomainError('SOURCE_STORAGE_WRITE_CONTRACT_INVALID')
        prefix = contract.get('prefix')
        if not isinstance(prefix, str) or not prefix or '\\' in prefix or prefix.startswith('/'):
            raise DomainError('SOURCE_STORAGE_WRITE_PREFIX_INVALID')
        self.prefix = prefix.rstrip('/') + '/'
        self.allowed_mime_types = frozenset(contract.get('allowed_mime_types') or ())
        if not self.allowed_mime_types or any(not isinstance(value, str) or '/' not in value
                                              for value in self.allowed_mime_types):
            raise DomainError('SOURCE_STORAGE_WRITE_MIME_POLICY_REQUIRED')
        self.max_upload_bytes = contract.get('max_upload_bytes')
        if type(self.max_upload_bytes) is not int or self.max_upload_bytes < 1024:
            raise DomainError('SOURCE_STORAGE_WRITE_SIZE_POLICY_REQUIRED')
        if contract.get('private') is not True or contract.get('allow_overwrite') is not False:
            raise DomainError('SOURCE_STORAGE_WRITE_POLICY_INVALID')
        allowed_roles = contract.get('allowed_roles', [])
        allowed_actor_ids = contract.get('allowed_actor_ids', [])
        if (not isinstance(allowed_roles, list) or not isinstance(allowed_actor_ids, list)
                or any(not isinstance(item, str) or not item.strip()
                       for item in (*allowed_roles, *allowed_actor_ids))):
            raise DomainError('SOURCE_STORAGE_WRITE_ACTOR_POLICY_REQUIRED')
        self.allowed_roles = frozenset(item.strip().upper() for item in allowed_roles)
        self.allowed_actor_ids = frozenset(item.strip() for item in allowed_actor_ids)
        self.cloud = cloud
        self.provider_id = cloud.provider_id
        self.bucket = cloud.bucket

    def allows(self, *, actor_id, role):
        roles = frozenset(str(item).strip().upper() for item in (getattr(self, 'allowed_roles', ()) or ()))
        actors = frozenset(str(item).strip() for item in (getattr(self, 'allowed_actor_ids', ()) or ()))
        return bool((str(role).upper() in roles or actor_id in actors))

    def upload(self, stream, *, resource_id, idempotency_key, content_type,
               expected_sha256=None):
        if not isinstance(resource_id, str) or not 1 <= len(resource_id) <= 30:
            raise DomainError('SOURCE_STORAGE_RESOURCE_ID_INVALID')
        if not isinstance(idempotency_key, str) or not re.fullmatch(r'[A-Za-z0-9._:-]{8,100}', idempotency_key):
            raise DomainError('SOURCE_WRITEBACK_IDEMPOTENCY_INVALID')
        if not isinstance(content_type, str) or content_type.lower() not in self.allowed_mime_types:
            raise DomainError('SOURCE_STORAGE_MIME_NOT_ALLOWED')
        # The key is server-generated and contains no caller path or filename.
        key = f'{self.prefix}{resource_id}/{hashlib.sha256(idempotency_key.encode()).hexdigest()}'
        try:
            existing = self.cloud.head_object(key)
        except DomainError as exc:
            if str(exc) != 'CLOUD_STORAGE_OBJECT_NOT_FOUND':
                raise
            existing = None
        if existing is not None:
            if expected_sha256 and existing.sha256 and existing.sha256.lower() != expected_sha256.lower():
                raise DomainError('SOURCE_STORAGE_IDEMPOTENCY_CONFLICT')
            return {'status': 'OBJECT_VERIFIED', 'object': existing, 'idempotent_replay': True}
        try:
            created = self.cloud.put_object(
                stream, object_key=key, content_type=content_type.lower(),
                max_size_bytes=self.max_upload_bytes, expected_sha256=expected_sha256)
        except UploadTooLargeError:
            raise DomainError('SOURCE_STORAGE_OBJECT_TOO_LARGE') from None
        except DomainError as exc:
            if str(exc) == 'CLOUD_STORAGE_CHECKSUM_MISMATCH':
                raise DomainError('SOURCE_STORAGE_CHECKSUM_MISMATCH') from None
            raise
        verified = self.cloud.head_object(key)
        if (verified.size != created.size or not verified.sha256
                or verified.sha256.lower() != created.sha256.lower()):
            raise DomainError('SOURCE_STORAGE_HEAD_VERIFICATION_FAILED')
        return {'status': 'OBJECT_VERIFIED', 'object': verified, 'idempotent_replay': False}

    def verify(self, *, bucket, object_key, size, source_hash):
        if bucket != self.bucket:
            raise DomainError('SOURCE_STORAGE_BUCKET_NOT_ALLOWED')
        item = self.cloud.head_object(object_key)
        if item.size != size or not item.sha256 or item.sha256.lower() != source_hash.lower():
            raise DomainError('SOURCE_STORAGE_HEAD_VERIFICATION_FAILED')
        return item
