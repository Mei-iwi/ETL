"""Three-level, metadata-only resolution from MASTER to an exact SOURCE record."""

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.metadata_search import metadata_allowed
from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.domain.search_access import denied


class SourceResolutionError(DomainError):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


class SourceResolutionService:
    """Resolve a permitted MASTER candidate through one exact source ID.

    This service never accepts SQL/collection names, never scans SOURCE, and
    never opens a file. SOURCE errors become a safe status so one offline
    dependency cannot take down search or leak connection details.
    """

    def __init__(self, uow, source, settings):
        self.uow, self.source, self.settings = uow, source, settings

    @staticmethod
    def _hash_matches(left, right):
        return (isinstance(left, str) and isinstance(right, str)
                and left.lower() == right.lower())

    @staticmethod
    def _content_availability(repo, version, import_row):
        if (version or {}).get('lifecycle_status') == 'METADATA_ONLY' or (import_row or {}).get('status') == 'METADATA_ONLY':
            return 'metadata-only'
        if version and repo.count('content_units', {'resource_version_id': version['id']}):
            return 'authorized-indexed'
        return 'metadata-only'

    @staticmethod
    def _safe_master(resource):
        return {
            'master_resource_id': resource['id'],
            'title': resource.get('title'),
            'resource_type_code': resource.get('resource_type_code'),
            'source_identity': resource.get('source_identity_hash'),
        }

    def resolve(self, resource_id, access, version_id=None):
        if not access.internal:
            raise SourceResolutionError('SEARCH_ACCESS_RESTRICTED', 403)
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id)
            if (not resource or not resource.get('is_active') or resource_deleted(resource)
                    or resource.get('publication_status') != 'PUBLISHED' or denied(resource)):
                raise SourceResolutionError('RESOURCE_NOT_FOUND', 404)
            if version_id is not None:
                version = repo.get('resource_versions', version_id)
                if not version or version.get('resource_id') != resource_id:
                    raise SourceResolutionError('RESOURCE_NOT_FOUND', 404)
            else:
                versions = repo.find('resource_versions', {'resource_id': resource_id},
                                     order=('-version_number', '-created_at', '-id'), limit=1)
                version = versions[0] if versions else None
            if not metadata_allowed(resource, version, access):
                raise SourceResolutionError('RESOURCE_NOT_FOUND', 404)
            result = self._safe_master(resource)
            result.update({
                'source_label': 'LOCAL' if resource.get('source_origin') == 'LOCAL' else
                    'SOURCE' if resource.get('source_origin') == 'POSTGRESQL' else 'UNKNOWN',
                'resolution_status': 'available',
                'content_availability': self._content_availability(repo, version, None),
                'source_record_available': resource.get('source_origin') == 'LOCAL',
                'version_id': version.get('id') if version else None,
            })
            if resource.get('source_origin') != 'POSTGRESQL':
                return result
            if not version:
                result.update(resolution_status='blocked', source_record_available=False,
                              content_availability='metadata-only')
                return result
            imports = repo.find('source_file_imports', {'target_version_id': version['id']}, limit=2)
            if len(imports) != 1:
                result.update(resolution_status='blocked', source_record_available=False,
                              content_availability='metadata-only')
                return result
            import_row = imports[0]
            result['content_availability'] = self._content_availability(repo, version, import_row)
            source_id = import_row.get('source_version_id')
            if (import_row.get('resource_id') != resource_id or not source_id
                    or not self._hash_matches(import_row.get('source_hash'), version.get('source_hash'))
                    or import_row.get('source_version_number') != version.get('version_number')):
                result.update(resolution_status='stale', source_record_available=False)
                return result
        getter = getattr(self.source, 'get_version', None)
        if getter is None:
            result.update(resolution_status='unavailable', source_record_available=False)
            return result
        try:
            source = getter(source_id)
        except Exception:
            result.update(resolution_status='unavailable', source_record_available=False)
            return result
        if not isinstance(source, dict):
            result.update(resolution_status='unavailable', source_record_available=False)
            return result
        if (source.get('source_version_id') != source_id or source.get('resource_id') != resource_id
                or source.get('source_version_number') != version.get('version_number')
                or not self._hash_matches(source.get('source_hash'), version.get('source_hash'))):
            result.update(resolution_status='stale', source_record_available=False)
            return result
        if (not source.get('resource_active', True) or source.get('resource_deleted_at') is not None
                or source.get('source_lifecycle_status') not in self.settings.source_file_allowed_statuses):
            result.update(resolution_status='blocked', source_record_available=False)
            return result
        result['source_record_available'] = True
        return result
