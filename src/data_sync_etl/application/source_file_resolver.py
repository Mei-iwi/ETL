"""Resolve trusted ownership; SOURCE bytes never go through managed storage.

Callers enforce their own application grant before resolving. Worker grants use
canonical SOURCE eligibility; HTTP additionally retains SearchAccess/index gates.
"""

import re
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import PurePosixPath

from data_sync_etl.domain.core import DomainError, SourceFileError
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.ports.source_files import ResolvedFileReference

SOURCE_METADATA_ONLY_BUCKET = '__SOURCE_METADATA_ONLY__'


class SourceFileResolver:
    def __init__(self, uow, source, files, storage, formats, settings, temporary, source_registry=None):
        self.uow, self.source, self.files = uow, source, files
        self.storage, self.formats, self.settings, self.temporary = storage, formats, settings, temporary
        self.source_registry = source_registry

    def _enabled(self):
        if not self.settings.source_reference_enabled:
            raise SourceFileError('SOURCE_REFERENCE_DISABLED')

    def _source_adapter(self, source_id):
        if self.source_registry is None:
            return self.source
        registrations = self.source_registry.registrations()
        resolved = source_id
        if resolved == 'postgresql' and len(registrations) == 1:
            resolved = registrations[0].source_id
        if resolved in {item.source_id for item in registrations}:
            try:
                return self.source_registry.adapter_for_source_id(resolved, 'exact_version_lookup')
            except DomainError:
                raise SourceFileError('SOURCE_CAPABILITY_BLOCKED') from None
        return self.source

    def _canonical(self, row):
        self._enabled()
        adapter = self._source_adapter(row['source_system'])
        if not hasattr(adapter, 'get_version'):
            raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        try:
            source = adapter.get_version(row['source_version_id'])
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

    def _canonical_metadata(self, row):
        """Validate source version metadata without resolving/storing its file path."""
        self._enabled()
        adapter = self._source_adapter(row['source_system'])
        if not hasattr(adapter, 'get_version'):
            raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        try:
            source = adapter.get_version(row['source_version_id'])
        except SourceFileError:
            raise
        except Exception:
            raise SourceFileError('SOURCE_UNAVAILABLE', transient=True) from None
        if source is None:
            raise SourceFileError('SOURCE_VERSION_MISSING')
        if (source.get('source_version_id') != row.get('source_version_id')
                or source.get('resource_id') != row.get('resource_id')
                or source.get('source_version_number') != row.get('source_version_number')
                or source.get('source_hash', '').lower() != row.get('source_hash', '').lower()):
            raise SourceFileError('SOURCE_VERSION_MUTATED')
        if (not source.get('resource_active', True) or source.get('resource_deleted_at') is not None
                or source.get('source_lifecycle_status') not in self.settings.source_file_allowed_statuses):
            raise SourceFileError('BLOCKED_SOURCE_POLICY')
        return source

    def _validate_source_lineage(self, version, row, source):
        """Require the bounded SOURCE ancestry and MASTER parent chain to match."""
        current_source = source
        current_version = version
        adapter = self._source_adapter(row['source_system'])
        seen = {source.get('source_version_id')}
        for _ in range(128):
            source_parent_id = current_source.get('parent_version_id')
            master_parent_id = current_version.get('parent_version_id')
            if source_parent_id is None:
                if master_parent_id is not None:
                    raise SourceFileError('BLOCKED_SOURCE_MAPPING')
                return
            if source_parent_id in seen or master_parent_id is None:
                raise SourceFileError('BLOCKED_SOURCE_MAPPING')
            seen.add(source_parent_id)
            try:
                parent = adapter.get_version(source_parent_id)
            except SourceFileError:
                raise
            except Exception:
                raise SourceFileError('SOURCE_UNAVAILABLE', transient=True) from None
            if (parent is None or parent.get('source_version_id') != source_parent_id
                    or parent.get('resource_id') != source.get('resource_id')):
                raise SourceFileError('BLOCKED_SOURCE_MAPPING')
            if (not parent.get('resource_active', True) or parent.get('resource_deleted_at') is not None
                    or parent.get('source_lifecycle_status') not in self.settings.source_file_allowed_statuses):
                raise SourceFileError('BLOCKED_SOURCE_POLICY')
            with self.uow() as repo:
                mappings = repo.find('source_file_imports', {
                    'source_system': row['source_system'], 'source_version_id': source_parent_id}, limit=2)
                master_parent = repo.get('resource_versions', master_parent_id)
            if (len(mappings) != 1 or mappings[0]['target_version_id'] != master_parent_id
                    or mappings[0]['resource_id'] != version['resource_id']
                    or mappings[0]['status'] not in ('METADATA_ONLY', 'IMPORTED', 'PROCESSING')
                    or master_parent is None or master_parent['resource_id'] != version['resource_id']
                    or master_parent['version_number'] != parent.get('source_version_number')
                    or master_parent['source_hash'].lower() != parent.get('source_hash', '').lower()):
                raise SourceFileError('BLOCKED_SOURCE_MAPPING')
            current_source, current_version = parent, master_parent
        raise SourceFileError('BLOCKED_SOURCE_MAPPING')

    def _reference(self, version, row=None):
        metadata_only = bool(row and row.get('storage_bucket') == SOURCE_METADATA_ONLY_BUCKET)
        source = (self._canonical_metadata(row) if metadata_only else self._canonical(row)) if row else None
        bucket = row['storage_bucket'] if row else version['storage_bucket']
        key = row['storage_object_key'] if row else version['storage_object_key']
        if metadata_only and source:
            bucket, key = source.get('storage_bucket'), source.get('storage_object_key')
            if not isinstance(bucket, str) or not bucket or not isinstance(key, str) or not key:
                raise SourceFileError('SOURCE_FILE_REFERENCE_UNAVAILABLE')
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
            provider = ('external' if metadata_only else
                        self.files.provider(bucket) if hasattr(self.files, 'provider') else 'local')
        return ResolvedFileReference(
            ownership='SOURCE_BACKED' if row else 'LOCAL_OWNED',
            target_version_id=version.get('id'), resource_id=version['resource_id'],
            source_version_id=row['source_version_id'] if row else None,
            provider=provider, bucket=bucket, opaque_locator=key,
            filename=PurePosixPath(key).name,
            mime='text/csv' if validator.extension == '.csv' else sorted(validator.mime_types)[0],
            expected_sha256=version['source_hash'].lower(),
            source_id=row['source_system'] if row else None, source_snapshot=source,
        )

    def _capability_source_id(self, ref):
        source_id = ref.source_id
        if self.source_registry is None:
            raise SourceFileError('SOURCE_CAPABILITY_BLOCKED')
        registrations = self.source_registry.registrations()
        if source_id in {item.source_id for item in registrations}:
            return source_id
        if source_id == 'postgresql' and len(registrations) == 1:
            return registrations[0].source_id
        raise SourceFileError('SOURCE_CAPABILITY_BLOCKED')

    def require_content_capabilities(self, ref, *, index=False):
        if ref.ownership != 'SOURCE_BACKED':
            return
        self._enabled()
        source_id = self._capability_source_id(ref)
        try:
            for capability in ('read_content', 'process_content'):
                self.source_registry.adapter_for_source_id(source_id, capability)
            if index:
                self.source_registry.adapter_for_source_id(source_id, 'index_derived_content')
        except DomainError as exc:
            code = str(exc).split(':', 1)[0]
            raise SourceFileError(code if code.startswith('SOURCE_') else 'SOURCE_CAPABILITY_BLOCKED') from None

    def derived_index_authorized(self, version_id, *, access):
        """Check retrieval grants, SOURCE eligibility and current registry policy."""
        from data_sync_etl.domain.metadata_search import metadata_allowed
        from data_sync_etl.domain.search_access import denied
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id)
            resource = repo.get('master_learning_resources', version['resource_id']) if version else None
            if not version or not resource:
                return False
            if resource.get('source_origin') == 'LOCAL':
                return True
            rows = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
            if (resource.get('source_origin') != 'POSTGRESQL'
                    or resource.get('publication_status') != 'PUBLISHED'
                    or not resource.get('is_active') or resource_deleted(resource)
                    or denied(resource) or denied(version)
                    or not metadata_allowed(resource, version, access)
                    or len(rows) != 1
                    or rows[0].get('status') not in ('PROCESSING', 'IMPORTED', 'METADATA_ONLY')
                    or rows[0].get('resource_id') != resource['id']
                    or rows[0].get('source_hash', '').lower() != version.get('source_hash', '').lower()
                    or not rows[0].get('source_resource_active', True)
                    or rows[0].get('source_lifecycle_status') not in self.settings.source_file_allowed_statuses
                    or rows[0].get('last_error_code') in ('SOURCE_VERSION_MUTATED', 'BLOCKED_SOURCE_MAPPING')):
                return False
            source_id = rows[0]['source_system']
        reference = ResolvedFileReference(
            ownership='SOURCE_BACKED', target_version_id=version_id, resource_id=resource['id'],
            source_version_id=rows[0]['source_version_id'], provider='external', bucket='',
            opaque_locator='', filename='', mime='', expected_sha256=version['source_hash'].lower(),
            source_id=source_id,
        )
        try:
            self.require_content_capabilities(reference, index=True)
            return True
        except SourceFileError:
            return False

    def resolve(self, version_id):
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id)
            resource = repo.get('master_learning_resources', version['resource_id']) if version else None
            rows = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
            if not version or not resource or not resource['is_active'] or resource_deleted(resource) or resource.get('source_missing_at') is not None:
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
            parent_version = repo.get('resource_versions', parent_id) if parent_id else None
        ref = self._reference(version, row)
        source_parent = ref.source_snapshot.get('parent_version_id')
        if (source_parent is None and parent_id is not None) or (source_parent is not None and (len(parents) != 1 or parents[0]['source_version_id'] != source_parent or parents[0]['resource_id'] != version['resource_id'])):
            raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        if source_parent is not None and (
                parent_version is None or parent_version['resource_id'] != version['resource_id']
                or parents[0]['status'] not in ('METADATA_ONLY', 'IMPORTED', 'PROCESSING')
                or parent_version['version_number'] != parents[0]['source_version_number']
                or parent_version['source_hash'].lower() != parents[0]['source_hash'].lower()):
            raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        self._validate_source_lineage(version, row, ref.source_snapshot)
        return ref

    def import_reference(self, row):
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', row['resource_id'])
            if resource is None or resource.get('source_origin') != 'POSTGRESQL':
                raise SourceFileError('BLOCKED_SOURCE_MAPPING')
        return self._reference({'resource_id': row['resource_id'], 'source_hash': row['source_hash']}, row)

    def _recheck(self, ref):
        if ref.ownership == 'SOURCE_BACKED':
            row = {**ref.source_snapshot, 'source_system': ref.source_id or 'postgresql'}
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
        # SOURCE bytes must never be materialized as a local file.
        raise SourceFileError('EXTERNAL_SOURCE_CONTENT_DISABLED')

    @contextmanager
    def open_verified_stream(self, ref, *, cancelled=None, index=False):
        if ref.ownership == 'LOCAL_OWNED':
            with self.temporary_path(ref, cancelled=cancelled) as path, path.open('rb') as stream:
                yield stream
            return
        self.require_content_capabilities(ref, index=index)
        self._recheck(ref)
        validator = self.formats.find_validator(ref.filename)
        maximum = min(validator.max_size_bytes, self.settings.source_content_max_bytes)
        before = self._revision(ref, cancelled)
        source = ref.source_snapshot or {}
        expected_size = source.get('source_file_size')
        if expected_size is not None and (type(expected_size) is not int or not 0 < expected_size <= maximum):
            raise SourceFileError('FILE_TOO_LARGE' if isinstance(expected_size, int) else 'INVALID_SOURCE_METADATA')
        stream = self.files.open_stream(ref.bucket, ref.opaque_locator,
                                        **({'cancelled': cancelled} if cancelled is not None else {}))
        buffer = BytesIO()
        try:
            observed = 0
            while True:
                self._check_cancelled(cancelled)
                block = stream.read(min(256 * 1024, maximum + 1 - observed))
                if not block:
                    break
                observed += len(block)
                if observed > maximum:
                    raise SourceFileError('FILE_TOO_LARGE')
                buffer.write(block)
            if expected_size is not None and observed != expected_size:
                raise SourceFileError('SOURCE_SIZE_MISMATCH')
            if sha256(buffer.getbuffer()).hexdigest() != ref.expected_sha256:
                raise SourceFileError('SOURCE_HASH_MISMATCH')
            after = self._revision(ref, cancelled)
            if before is not None and after is not None and before != after:
                raise SourceFileError('SOURCE_VERSION_MUTATED')
            self._recheck(ref)
            buffer.seek(0)
            yield buffer
        finally:
            stream.close()
            buffer.close()

    def display_reference(self, ref):
        if ref.ownership != 'LOCAL_OWNED':
            return {'ownership': 'SOURCE_BACKED', 'provider': 'external',
                    'availability': 'METADATA_ONLY', 'can_open_original': False,
                    'original_url': None}
        self._recheck(ref)
        return {'ownership': ref.ownership, 'provider': ref.provider,
                'availability': 'UNKNOWN', 'can_open_original': False, 'original_url': None}
