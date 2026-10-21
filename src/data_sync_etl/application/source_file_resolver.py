"""Resolve trusted ownership; SOURCE bytes never go through managed storage.

Callers enforce their own application grant before resolving. Worker grants use
canonical SOURCE eligibility; HTTP additionally retains SearchAccess/index gates.
"""

import os
import re
import time
from contextlib import contextmanager
from hashlib import sha256
from pathlib import PurePosixPath

from data_sync_etl.domain.core import DomainError, SourceFileError
from data_sync_etl.ports.source_files import ResolvedFileReference

CHUNK = 256 * 1024


class SourceFileResolver:
    def __init__(self, uow, source, files, storage, formats, settings, temporary):
        self.uow, self.source, self.files = uow, source, files
        self.storage, self.formats, self.settings, self.temporary = storage, formats, settings, temporary

    def _enabled(self):
        if not self.settings.source_reference_enabled:
            raise SourceFileError('SOURCE_REFERENCE_DISABLED')

    def _canonical(self, row):
        self._enabled()
        if row['source_system'] != 'postgresql' or not hasattr(self.source, 'get_version'):
            raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        try:
            source = self.source.get_version(row['source_version_id'])
        except SourceFileError:
            raise
        except Exception:
            raise SourceFileError('SOURCE_UNAVAILABLE', transient=True) from None
        if source is None:
            raise SourceFileError('SOURCE_VERSION_MISSING')
        keys = ('source_version_id', 'resource_id', 'source_version_number', 'storage_bucket', 'storage_object_key')
        if any(source.get(k) != row.get(k) for k in keys) or source.get('source_hash', '').lower() != row['source_hash'].lower():
            raise SourceFileError('SOURCE_VERSION_MUTATED')
        if not source.get('resource_active', True) or source.get('resource_deleted_at') is not None or source.get('source_lifecycle_status') not in self.settings.source_file_allowed_statuses:
            raise SourceFileError('BLOCKED_SOURCE_POLICY')
        return source

    def _reference(self, version, row=None):
        source = self._canonical(row) if row else None
        bucket = row['storage_bucket'] if row else version['storage_bucket']
        key = row['storage_object_key'] if row else version['storage_object_key']
        if '://' in bucket or '://' in key:
            raise SourceFileError('INVALID_SOURCE_LINK')
        if not re.fullmatch('[0-9a-fA-F]{64}', version['source_hash']):
            raise SourceFileError('INVALID_SOURCE_HASH')
        try:
            validator = self.formats.find_validator(key)
        except DomainError:
            raise SourceFileError('UNSUPPORTED_SOURCE_FORMAT') from None
        provider = 'managed'
        if row:
            provider = self.files.provider(bucket) if hasattr(self.files, 'provider') else 'local'
        return ResolvedFileReference(
            ownership='SOURCE_BACKED' if row else 'LOCAL_OWNED',
            target_version_id=version.get('id'), resource_id=version['resource_id'],
            source_version_id=row['source_version_id'] if row else None,
            provider=provider, bucket=bucket, opaque_locator=key,
            filename=PurePosixPath(key).name,
            mime='text/csv' if validator.extension == '.csv' else sorted(validator.mime_types)[0],
            expected_sha256=version['source_hash'].lower(), source_snapshot=source,
        )

    def resolve(self, version_id):
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id)
            resource = repo.get('master_learning_resources', version['resource_id']) if version else None
            rows = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
            if not version or not resource or not resource['is_active'] or resource.get('deleted_at') is not None or resource.get('source_missing_at') is not None:
                raise SourceFileError('FILE_NOT_FOUND')
            origin = resource.get('source_origin')
            if origin == 'LOCAL' and not rows:
                return self._reference(version)
            if origin != 'POSTGRESQL' or len(rows) != 1:
                raise SourceFileError('BLOCKED_SOURCE_MAPPING')
            row = rows[0]
            if row['last_error_code'] in ('SOURCE_VERSION_MUTATED', 'BLOCKED_SOURCE_MAPPING'):
                raise SourceFileError(row['last_error_code'])
            if (row['resource_id'], row['source_version_number'], row['source_hash'].lower()) != (version['resource_id'], version['version_number'], version['source_hash'].lower()):
                raise SourceFileError('BLOCKED_SOURCE_MAPPING')
            parent_id = version.get('parent_version_id')
            parents = repo.find('source_file_imports', {'target_version_id': parent_id}, limit=2) if parent_id else []
        ref = self._reference(version, row)
        source_parent = ref.source_snapshot.get('parent_version_id')
        if (source_parent is None and parent_id is not None) or (source_parent is not None and (len(parents) != 1 or parents[0]['source_version_id'] != source_parent or parents[0]['resource_id'] != version['resource_id'])):
            raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        return ref

    def import_reference(self, row):
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', row['resource_id'])
            if resource is None or resource.get('source_origin') != 'POSTGRESQL':
                raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        return self._reference({'resource_id': row['resource_id'], 'source_hash': row['source_hash']}, row)

    def _recheck(self, ref):
        if ref.ownership == 'SOURCE_BACKED':
            row = {**ref.source_snapshot, 'source_system': 'postgresql'}
            current = self._canonical(row)
            if current != ref.source_snapshot:
                raise SourceFileError('SOURCE_VERSION_MUTATED')
            if ref.target_version_id:
                current_ref = self.resolve(ref.target_version_id)
                if current_ref != ref:
                    raise SourceFileError('SOURCE_VERSION_MUTATED')

    def _revision(self, ref, cancelled=None):
        if hasattr(self.files, 'revision'):
            return self.files.revision(ref.bucket, ref.opaque_locator,
                                       **({'cancelled': cancelled} if cancelled is not None else {}))
        return None

    @staticmethod
    def _check_cancelled(cancelled):
        if cancelled is not None and cancelled.is_set():
            raise SourceFileError('SOURCE_REQUEST_CANCELLED')

    @contextmanager
    def temporary_path(self, ref, *, cancelled=None):
        if ref.ownership == 'LOCAL_OWNED':
            if not self.storage.exists(ref.bucket, ref.opaque_locator):
                raise SourceFileError('FILE_NOT_FOUND')
            yield self.storage.get_local_path(ref.bucket, ref.opaque_locator)
            return
        self._recheck(ref)
        self._check_cancelled(cancelled)
        validator = self.formats.find_validator(ref.filename)
        with self.temporary.scope() as directory:
            deadline = time.monotonic() + self.settings.source_read_timeout_seconds
            revision = self._revision(ref, cancelled)
            path = directory / ('source' + validator.extension)
            digest, size = sha256(), 0
            try:
                options = {'cancelled': cancelled} if cancelled is not None else {}
                with self.files.open_stream(ref.bucket, ref.opaque_locator, **options) as stream, path.open('xb') as target:
                    os.chmod(path, 0o600)
                    while True:
                        self._check_cancelled(cancelled)
                        if time.monotonic() > deadline:
                            raise SourceFileError('SOURCE_TIMEOUT', transient=True)
                        block = stream.read(CHUNK)
                        self._check_cancelled(cancelled)
                        if not block:
                            break
                        size += len(block)
                        if size > validator.max_size_bytes:
                            raise SourceFileError('FILE_TOO_LARGE')
                        digest.update(block)
                        target.write(block)
                if digest.hexdigest() != ref.expected_sha256:
                    raise SourceFileError('SOURCE_HASH_MISMATCH')
                with path.open('rb') as header:
                    validator.validate(validator.extension, ref.mime, header.read(16), size)
                validator.validate_path(path)
                self._recheck(ref)
                if revision != self._revision(ref, cancelled):
                    raise SourceFileError('SOURCE_REVISION_CHANGED')
                if time.monotonic() > deadline:
                    raise SourceFileError('SOURCE_TIMEOUT', transient=True)
                self._check_cancelled(cancelled)
                yield path
                self._recheck(ref)
                if revision != self._revision(ref, cancelled):
                    raise SourceFileError('SOURCE_REVISION_CHANGED')
            except SourceFileError:
                raise
            except DomainError as exc:
                code = str(exc).split(':', 1)[0]
                safe = {'UNSUPPORTED_TEXT_ENCODING', 'INVALID_IMAGE', 'OPENXML_LIMIT_EXCEEDED', 'INVALID_OPENXML_CONTAINER', 'INVALID_OPENXML_XML', 'OPENXML_TYPE_MISMATCH', 'UNSUPPORTED_OPENXML_MACROS'}
                raise SourceFileError(code if code in safe else 'INVALID_PDF' if validator.extension == '.pdf' else 'INVALID_SOURCE_FILE') from None
            except OSError:
                raise SourceFileError('SOURCE_UNAVAILABLE', transient=True) from None

    def display_reference(self, ref):
        self._recheck(ref)
        url = None
        if ref.ownership == 'SOURCE_BACKED':
            self._revision(ref)
        if ref.ownership == 'SOURCE_BACKED' and ref.provider == 'google_drive':
            # The HTTP caller has an application file grant; revision() verifies
            # provider capability/root membership. Drive enforces browser-user
            # permissions when the user opens this credential-free URL.
            url = self.files.original_url(ref.bucket, ref.opaque_locator)
        return {'ownership': ref.ownership, 'provider': ref.provider,
                'availability': 'UNKNOWN', 'can_open_original': url is not None, 'original_url': url}

    @contextmanager
    def open_verified_stream(self, ref):
        with self.temporary_path(ref) as path, path.open('rb') as stream:
            yield stream
