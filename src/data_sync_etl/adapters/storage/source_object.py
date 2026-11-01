"""Read-only S3-compatible SOURCE object storage.

SOURCE bytes are streamed into bounded memory by ``SourceFileResolver`` and
never written to ETL-managed storage.  This adapter only exposes an explicit
bucket allowlist; it cannot list buckets or follow caller-supplied URLs.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath

from data_sync_etl.adapters.storage.object import S3CompatibleObjectStorageAdapter
from data_sync_etl.domain.core import DomainError, SourceFileError

_ERROR_MAP = {
    "CLOUD_STORAGE_OBJECT_NOT_FOUND": "FILE_NOT_FOUND",
    "CLOUD_STORAGE_ACCESS_DENIED": "PERMISSION_DENIED",
    "CLOUD_STORAGE_UNAVAILABLE": "SOURCE_STORAGE_UNAVAILABLE",
    "CLOUD_STORAGE_TRANSIENT": "SOURCE_STORAGE_UNAVAILABLE",
    "CLOUD_STORAGE_REQUEST_FAILED": "SOURCE_STORAGE_UNAVAILABLE",
}


class SourceObjectStorageReader:
    """Expose one configured private S3 bucket as a source file reader."""

    def __init__(self, cloud: S3CompatibleObjectStorageAdapter, *, read_url_ttl_seconds: int = 60):
        self.cloud = cloud
        self.bucket = cloud.bucket
        self.read_url_ttl_seconds = read_url_ttl_seconds

    def _check_bucket(self, bucket):
        if bucket != self.bucket:
            raise SourceFileError("SOURCE_BUCKET_NOT_ALLOWED")

    @staticmethod
    def _translate(error):
        code = str(error)
        return SourceFileError(_ERROR_MAP.get(code, "SOURCE_STORAGE_UNAVAILABLE"),
                               transient=code in {"CLOUD_STORAGE_UNAVAILABLE", "CLOUD_STORAGE_TRANSIENT",
                                                  "CLOUD_STORAGE_REQUEST_FAILED"})

    def check(self):
        try:
            self.cloud.validate_config()
        except DomainError as error:
            raise self._translate(error) from None

    def open_stream(self, bucket, object_key, *, cancelled=None):
        self._check_bucket(bucket)
        if cancelled is not None and cancelled.is_set():
            raise SourceFileError("SOURCE_REQUEST_CANCELLED")
        try:
            return self.cloud.get_stream(object_key)
        except DomainError as error:
            raise self._translate(error) from None

    def revision(self, bucket, object_key, *, cancelled=None):
        self._check_bucket(bucket)
        if cancelled is not None and cancelled.is_set():
            raise SourceFileError("SOURCE_REQUEST_CANCELLED")
        try:
            item = self.cloud.head_object(object_key)
        except DomainError as error:
            raise self._translate(error) from None
        # ETag is provider metadata, not an integrity assertion.  Resolver
        # still computes and verifies the canonical SHA-256 after streaming.
        return item.size, item.etag, item.content_type

    def original_url(self, bucket, object_key):
        self._check_bucket(bucket)
        return None

    def viewer_reference(self, bucket, object_key):
        self._check_bucket(bucket)
        try:
            item = self.cloud.head_object(object_key)
            viewer = self.cloud.presign_read(object_key, ttl_seconds=self.read_url_ttl_seconds)
        except DomainError as error:
            raise self._translate(error) from None
        # Keep the storage locator out of the response. The URL is short-lived
        # and is emitted only after the caller has passed SOURCE ACL checks.
        opaque_id = hashlib.sha256(f'{bucket}:{object_key}'.encode()).hexdigest()
        return {
            'source_file_id': opaque_id,
            'file_name': PurePosixPath(object_key).name,
            'mime_type': item.content_type,
            'revision': item.etag,
            'viewer_url': viewer,
            'embedded_url': viewer,
        }
