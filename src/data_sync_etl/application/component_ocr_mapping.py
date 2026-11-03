"""Server-side trust check for a component processing projection event."""
from data_sync_etl.domain.search_access import SearchAccess


class TrustedComponentMapping:
    """Verify projection identities using Master mappings and registered source policy.

    This callback does not accept credentials or identity assertions from an
    HTTP request. It is intended for a separately authenticated component feed.
    The component feed and worker are not enabled by this class.
    """

    def __init__(self, uow, identity_store, source_resolver):
        self.uow = uow
        self.identity_store = identity_store
        self.source_resolver = source_resolver

    def __call__(self, kind, snapshot):
        if kind not in ('job', 'page', 'unit'):
            return False
        version_id = snapshot.get('master_version_id')
        with self.uow() as repo:
            version = repo.get('resource_versions', version_id)
            if not version:
                return False
            resource = repo.get('master_learning_resources', version['resource_id'])
            imports = repo.find('source_file_imports', {'target_version_id': version_id}, limit=2)
        if (not resource or resource.get('source_origin') != 'POSTGRESQL'
                or resource.get('source_missing_at') is not None or len(imports) != 1):
            return False
        row = imports[0]
        if (row.get('resource_id') != version['resource_id']
                or not version.get('source_hash') or not row.get('source_hash')
                or row['source_hash'].lower() != version['source_hash'].lower()):
            return False
        mapping = self.identity_store.resource_mapping(version['resource_id'])
        if (not mapping or mapping.get('status') != 'ACTIVE'
                or mapping.get('namespace') != snapshot.get('namespace')
                or mapping.get('source_id') != row.get('source_system')):
            return False
        if kind != 'page':
            if (snapshot.get('master_resource_id') != version['resource_id']
                    or snapshot.get('source_version_id') != row.get('source_version_id')):
                return False
        if kind == 'job' and snapshot.get('source_hash') != version['source_hash'].lower():
            return False
        return self.source_resolver.derived_index_authorized(
            version_id, access=SearchAccess(scope='authenticated_admin'))
