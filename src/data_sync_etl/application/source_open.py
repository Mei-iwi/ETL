"""Resolve an exact source file reference without downloading its bytes."""

from ipaddress import ip_address
from urllib.parse import parse_qsl, urlsplit

from data_sync_etl.application.source_resolution import SourceResolutionError, same_import_snapshot
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.metadata_search import metadata_allowed
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.domain.search_access import denied
from data_sync_etl.domain.source_file_reference import SourceFileReference
from data_sync_etl.domain.source_status import normalize_source_status, source_status_allowed


class SourceOpenResolver:
    def __init__(self, uow, registry, router, source_resolution, file_sources=None):
        self.uow, self.registry, self.router = uow, registry, router
        self.source_resolution = source_resolution
        self.file_sources = file_sources

    @staticmethod
    def _allowed_viewer(url, hosts):
        if not isinstance(url, str) or len(url) > 2048:
            return False
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or '').lower().rstrip('.')
            if (parsed.scheme != 'https' or not host or parsed.username or parsed.password
                    or parsed.port not in (None, 443) or parsed.fragment or host not in hosts
                    or any(key.casefold() in {'token', 'access_token', 'auth', 'authorization',
                                              'signature', 'sig', 'credential', 'key', 'jwt', 'session',
                                              'authkey', 'resourcekey'}
                           for key, _ in parse_qsl(parsed.query, keep_blank_values=True))):
                return False
            ip_address(host)
        except ValueError:
            # A non-IP hostname is expected; malformed ports/URLs fail closed.
            try:
                parsed = urlsplit(url)
                host = (parsed.hostname or '').lower().rstrip('.')
                return (parsed.scheme == 'https' and bool(host) and not parsed.username
                        and not parsed.password and parsed.port in (None, 443)
                        and not parsed.fragment and host in hosts
                        and not any(key.casefold() in {'token', 'access_token', 'auth', 'authorization',
                                                       'signature', 'sig', 'credential', 'key', 'jwt', 'session',
                                                       'authkey', 'resourcekey'}
                                    for key, _ in parse_qsl(parsed.query, keep_blank_values=True)))
            except ValueError:
                return False
        return False

    def resolve(self, resource_id, access, version_id=None):
        if not access.internal:
            raise SourceResolutionError('SEARCH_ACCESS_RESTRICTED', 403)
        if not getattr(self.source_resolution.settings, 'source_reference_enabled', False):
            raise SourceResolutionError('SOURCE_REFERENCE_DISABLED', 503)
        # Reuse the established publication, ACL, identity and exact-version checks.
        resolved = self.source_resolution.resolve(resource_id, access, version_id, verify_content=False)
        # A revoked or otherwise policy-blocked SOURCE must fail closed before
        # looking up viewer metadata.  Do not turn a policy denial into a
        # misleading "viewer unsupported" response (and never perform SOURCE
        # I/O after the grant has been revoked).
        if resolved.get('content_availability') == 'revoked':
            raise SourceResolutionError('BLOCKED_SOURCE_POLICY', 403)
        result = {
            'master_resource_id': resource_id,
            'version_id': resolved.get('version_id'),
            'status': 'SOURCE_OPEN_UNSUPPORTED',
            'files': [],
        }
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id)
            version = repo.get('resource_versions', result['version_id']) if result['version_id'] else None
            if (not metadata_allowed(resource, version, access) or not resource.get('is_active')
                    or resource_deleted(resource) or resource.get('publication_status') != 'PUBLISHED'
                    or denied(resource)):
                raise SourceResolutionError('RESOURCE_NOT_FOUND', 404)
            if resource.get('source_origin') != 'POSTGRESQL' or version is None:
                result['status'] = 'METADATA_ONLY'
                return result
            imports = repo.find('source_file_imports', {'target_version_id': version['id']}, limit=2)
            if len(imports) != 1:
                result['status'] = 'METADATA_ONLY'
                return result
            imported = imports[0]
            imported_status = normalize_source_status(imported.get('source_lifecycle_status'))
            if (imported_status in {'PRIVATE', 'REJECTED', 'DELETED'}
                    or not source_status_allowed(imported.get('source_lifecycle_status'),
                                                 self.source_resolution.settings.source_file_allowed_statuses)):
                raise SourceResolutionError('BLOCKED_SOURCE_POLICY', 403)
            try:
                route = self.router.route_resource(resource)
            except DomainError:
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
            expected_source_system = route.source_id
            legacy_allowed = route.legacy_id_match and route.locator.backend.casefold() == 'postgresql'
            if (imported.get('source_system') != expected_source_system
                    and not (legacy_allowed and imported.get('source_system') == 'postgresql')):
                result['status'] = 'VERSION_STALE'
                return result
            if (imported.get('resource_id') != resource_id or imported.get('status') not in
                    ('METADATA_ONLY', 'IMPORTED', 'PROCESSING')
                    or imported.get('source_version_number') != version.get('version_number')):
                result['status'] = 'VERSION_STALE'
                return result
            source_version_id = imported.get('source_version_id')
            if not isinstance(source_version_id, str) or not source_version_id or len(source_version_id) > 30:
                result['status'] = 'VERSION_STALE'
                return result
            registration = next((row for row in self.registry.registrations()
                                 if row.source_id == route.source_id), None)
            if registration is None or not registration.capabilities.exact_version_lookup:
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
            try:
                adapter = self.registry.adapter_for_source_id(route.source_id, 'exact_version_lookup')
            except DomainError:
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
        getter = getattr(adapter, 'get_version', None)
        if not callable(getter):
            result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
            return result
        try:
            source_version = getter(source_version_id)
        except Exception:
            result['status'] = 'SOURCE_UNAVAILABLE'
            return result
        if (not isinstance(source_version, dict)
                or source_version.get('source_version_id') != source_version_id
                or source_version.get('resource_id') != route.locator.source_record_id
                or source_version.get('source_version_number') != version['version_number']):
            result['status'] = 'VERSION_STALE'
            return result
        if not (registration.capabilities.file_reference_lookup
                and registration.capabilities.external_viewer):
            result['status'] = ('SOURCE_AUTH_REQUIRED'
                                if source_version.get('source_auth_required') else 'METADATA_ONLY')
            return result
        list_refs = getattr(adapter, 'list_file_references', None)
        if callable(list_refs):
            try:
                raw_files = list_refs(source_version_id)
            except Exception:
                result['status'] = 'SOURCE_UNAVAILABLE'
                return result
        else:
            viewer_lookup = getattr(self.file_sources, 'viewer_reference', None)
            bucket, object_key = source_version.get('storage_bucket'), source_version.get('storage_object_key')
            if not callable(viewer_lookup) or not isinstance(bucket, str) or not isinstance(object_key, str):
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
            try:
                viewer = viewer_lookup(bucket, object_key)
            except Exception as exc:
                code = getattr(exc, 'code', '')
                result['status'] = ('ACCESS_DENIED' if code in ('DRIVE_PERMISSION_DENIED', 'PERMISSION_DENIED')
                                    else 'VERSION_STALE' if code in ('DRIVE_FILE_NOT_FOUND', 'FILE_NOT_FOUND', 'DRIVE_FILE_CHANGED')
                                    else 'SOURCE_UNAVAILABLE')
                return result
            if viewer is None:
                raw_files = []
            else:
                raw_files = [{
                    'source_resource_id': route.locator.source_record_id,
                    'source_version_id': source_version_id,
                    **viewer,
                    'access_status': 'ALLOWED',
                }]
        if not isinstance(raw_files, (list, tuple)) or len(raw_files) > 100:
            result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
            return result
        hosts = set(registration.viewer_hosts)
        files = []
        for raw in raw_files:
            if not isinstance(raw, dict):
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
            if (raw.get('source_version_id') != source_version_id
                    or raw.get('source_resource_id') != route.locator.source_record_id):
                result['status'] = 'VERSION_STALE'
                return result
            try:
                reference = SourceFileReference.model_validate(raw)
            except Exception:
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
            if reference.access_status == 'DENIED':
                result['status'] = 'ACCESS_DENIED'
                return result
            if reference.access_status == 'UNAVAILABLE':
                result['status'] = 'SOURCE_UNAVAILABLE'
                return result
            if reference.access_status == 'AUTH_REQUIRED':
                result['status'] = 'SOURCE_AUTH_REQUIRED'
            elif reference.access_status == 'METADATA_ONLY':
                result['status'] = 'METADATA_ONLY'
            elif not self._allowed_viewer(reference.viewer_url, hosts):
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
            if (reference.embedded_url is not None
                    and not self._allowed_viewer(reference.embedded_url, hosts)):
                result['status'] = 'SOURCE_OPEN_UNSUPPORTED'
                return result
            safe_file = reference.model_dump(exclude={'source_resource_id', 'source_version_id'})
            if reference.access_status != 'ALLOWED':
                safe_file['viewer_url'] = None
                safe_file['embedded_url'] = None
            files.append(safe_file)
        if files and all(row['access_status'] == 'ALLOWED' for row in files):
            result['status'] = 'EXTERNAL_VIEWER_AVAILABLE'
        elif not files:
            result['status'] = 'METADATA_ONLY'
        result['files'] = files
        try:
            current_source_version = getter(source_version_id)
        except Exception:
            result.update(status='SOURCE_UNAVAILABLE', files=[])
            return result
        if (not isinstance(current_source_version, dict)
                or current_source_version.get('source_version_id') != source_version_id
                or current_source_version.get('resource_id') != route.locator.source_record_id
                or current_source_version.get('source_version_number') != version.get('version_number')
                or current_source_version.get('source_hash') != source_version.get('source_hash')
                or not current_source_version.get('resource_active', True)
                or current_source_version.get('resource_deleted_at') is not None
                or current_source_version.get('resource_publication_status', 'PUBLISHED') != 'PUBLISHED'):
            result.update(status='VERSION_STALE', files=[])
            return result
        # Revalidate MASTER ACL, version and source mapping after SOURCE I/O.
        with self.uow() as repo:
            current = repo.get('master_learning_resources', resource_id)
            current_version = repo.get('resource_versions', version['id'])
            current_imports = repo.find('source_file_imports', {'target_version_id': version['id']}, limit=2)
            try:
                current_route = self.router.route_resource(current) if current else None
            except Exception:
                current_route = None
            if (not metadata_allowed(current, current_version, access) or current != resource
                    or current_version != version or len(current_imports) != 1
                    or not same_import_snapshot(current_imports[0], imported) or current_route is None
                    or current_route.locator != route.locator or current_route.adapter is not adapter):
                result.update(status='VERSION_STALE', files=[])
        return result
