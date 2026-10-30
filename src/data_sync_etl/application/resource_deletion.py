"""Row-locked, revision-fenced MASTER visibility changes; no external RPC."""
from data_sync_etl.domain.core import now
from data_sync_etl.domain.source_ownership import state_hash
from data_sync_etl.security.auth import AuthError


class ResourceDeletion:
    def __init__(self, uow):
        self.uow = uow

    def change(self, resource_id, principal, *, restore=False, reason=None, expected_revision=None):
        if principal is None or principal.role != 'ADMIN':
            raise AuthError('AUTH_FORBIDDEN', 403)
        with self.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id, lock=True)
            if resource is None:
                raise AuthError('RESOURCE_NOT_FOUND', 404)
            revision = resource['deletion_revision']
            if expected_revision is not None and expected_revision != revision:
                raise AuthError('RESOURCE_DELETION_CONFLICT', 409)
            deleted = resource['master_deleted_at'] is not None
            if resource['source_origin'] not in ('LOCAL', 'POSTGRESQL'):
                raise AuthError('RESOURCE_ORIGIN_UNVERIFIED', 409)
            if restore:
                if (resource['deleted_at'] is not None or not resource['is_active'] or resource['source_missing_at']
                        or resource['publication_status'] != 'PUBLISHED'):
                    raise AuthError('RESOURCE_RESTORE_SOURCE_RESTRICTED', 409)
                if resource['source_origin'] == 'POSTGRESQL' and resource['source_state_hash'] != state_hash(resource):
                    raise AuthError('RESOURCE_RESTORE_SOURCE_RESTRICTED', 409)
                versions = repo.find('resource_versions', {'resource_id': resource_id}, order=('-version_number',), limit=1)
                if versions and versions[0]['lifecycle_status'] not in ('METADATA_ONLY', 'INGESTED', 'APPROVED', 'PUBLISHED'):
                    raise AuthError('RESOURCE_RESTORE_VERSION_NOT_READY', 409)
            if deleted == (not restore):
                return {'resource_id': resource_id, 'deleted': deleted, 'revision': revision, 'changed': False}
            stamp, revision = now(), revision + 1
            repo.update('master_learning_resources', resource_id, {
                'master_deleted_at': None if restore else stamp,
                'master_deleted_by': None if restore else principal.id,
                'master_deletion_reason': None if restore else reason,
                'deletion_revision': revision})
            repo.insert('resource_deletion_events', {
                'resource_id': resource_id, 'revision': revision, 'action': 'RESTORE' if restore else 'DELETE',
                'actor_id': principal.id, 'reason': reason, 'origin': 'MASTER_VISIBILITY_OVERRIDE', 'created_at': stamp})
        return {'resource_id': resource_id, 'deleted': not restore, 'revision': revision, 'changed': True}
