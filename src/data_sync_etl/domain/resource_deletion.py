"""MASTER-local visibility override, independent of SOURCE lifecycle fields."""

LOCAL_DELETION_FIELDS = frozenset({'master_deleted_at', 'master_deleted_by',
                                  'master_deletion_reason', 'deletion_revision'})


def resource_deleted(resource):
    return bool(resource and (resource.get('deleted_at') is not None
                              or resource.get('master_deleted_at') is not None))


def require_live_version(repo, version_id):
    from data_sync_etl.domain.core import DomainError
    version = repo.get('resource_versions', version_id)
    resource = repo.get('master_learning_resources', version['resource_id']) if version else None
    if not resource or resource_deleted(resource) or not resource['is_active'] or resource.get('source_missing_at'):
        raise DomainError('RESOURCE_NOT_AVAILABLE')
    return resource
