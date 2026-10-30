"""Explicit route capabilities, with independent resource grants for non-admins."""

import re

from data_sync_etl.domain.resource_deletion import resource_deleted
from data_sync_etl.security.auth import AuthError


def owns_resource(principal, resource):
    return principal.mode != 'master_users' or (
        principal.id.startswith('master:') and resource is not None
        and resource.get('created_by') == principal.id.removeprefix('master:'))


def can_read_public_file(container, version_id):
    """Reading a public file requires EVERY unit and the current index to pass.

    A search hit is insufficient: other units in that file may be restricted.
    This predicate never grants ownership or write capabilities.
    """
    from data_sync_etl.domain.search import SearchError
    from data_sync_etl.domain.search_access import SearchAccess

    try:
        container.search.file_version(version_id, access=SearchAccess(
            'authenticated_user', allow_public=True, allow_shared_unreviewed=True))
    except SearchError:
        return False
    return True


def public_metadata_version_ids(container, resource_id):
    """Same published metadata/verified import policy as search; no file grant."""
    from data_sync_etl.domain.metadata_search import metadata_allowed, metadata_mapping_allowed
    from data_sync_etl.domain.search_access import SearchAccess

    access = SearchAccess('authenticated_user', allow_public=True, allow_shared_unreviewed=True)
    with container.uow() as repo:
        rows = repo.metadata_snapshots({'resource_id': resource_id}, access, limit=1)
    return {version['id'] for _, resource, version, _, imports in rows
            if metadata_allowed(resource, version, access) and metadata_mapping_allowed(
                resource, version, imports, container.settings.source_file_allowed_statuses)}


def authorize(request, principal):
    path, method = request.url.path, request.method
    if principal.must_change_password and path not in ('/api/v1/auth/me', '/api/v1/auth/change-password', '/api/v1/auth/logout'):
        raise AuthError('AUTH_PASSWORD_CHANGE_REQUIRED', 403)
    if principal.role == 'ADMIN':
        return
    if principal.mode not in ('db_users', 'master_users'):
        raise AuthError('AUTH_FORBIDDEN', 403)
    if path in ('/api/v1/auth/me', '/api/v1/auth/change-password'):
        return
    if path == '/api/v1/auth/profile' and method in ('GET', 'PATCH'):
        return  # Endpoint resolves only this session's explicit MASTER -> Mongo identity.
    catalogs = ('/api/v1/subjects', '/api/v1/providers', '/api/v1/resource-types', '/api/v1/grade-levels')
    if method == 'GET' and (path in catalogs or path in ('/api/v1/search/model-status', '/api/v1/resources')):
        return
    if ((path in ('/api/v1/search', '/api/v1/search/page') and method == 'POST')
            or (path.startswith('/api/v1/chunks/') and path.endswith('/context') and method == 'GET')):
        return  # SearchAccess verifies explicit resource grants and eligibility.
    if method == 'GET' and re.fullmatch(r'/api/v1/resources/[^/]+/(source-resolution|open)', path):
        return  # SourceResolutionService validates the same published metadata policy as search.
    if ('/source-writeback' in path or path == '/api/v1/source-writeback/status'):
        # Only independently granted operators may reach the service. The
        # service additionally requires a revocable SOURCE write grant.
        if principal.role == 'OPERATOR' and 'sync' in principal.permissions:
            return
        raise AuthError('AUTH_FORBIDDEN', 403)
    version = re.fullmatch(r'/api/v1/resource-versions/([^/]+)/(file|source-info|chunks|content-units|index-status|etl-status|process|postprocess|index|reindex|ocr-jobs|processing-options|bilingual|processing-source|translations/retry|exports/(?:json|txt|srt|vtt)|segments/[^/]+/frame)', path)
    if version:
        if method != 'GET' and principal.role != 'OPERATOR':
            raise AuthError('AUTH_FORBIDDEN', 403)
        with request.app.state.container.uow() as repo:
            row = repo.get('resource_versions', version[1])
            if not row:
                raise AuthError('AUTH_RESOURCE_FORBIDDEN', 403)
            resource = repo.get('master_learning_resources', row['resource_id'])
            granted = row['resource_id'] in principal.resource_ids and owns_resource(principal, resource)
        if not granted:
            metadata_status = (method == 'GET' and version[2] in ('index-status', 'file', 'source-info', 'processing-source')
                               and row['id'] in public_metadata_version_ids(request.app.state.container, row['resource_id']))
            if not (metadata_status or (method == 'GET' and version[2] in ('file', 'source-info', 'index-status')
                    and can_read_public_file(request.app.state.container, row['id']))):
                raise AuthError('AUTH_RESOURCE_FORBIDDEN', 403)
        if method == 'GET':
            return  # Domain SearchAccess still checks visibility, review and ownership.
    if method == 'PATCH' and re.fullmatch(r'/api/v1/resources/[^/]+/description', path):
        with request.app.state.container.uow() as repo:
            resource = repo.get('master_learning_resources', path.split('/')[4])
        if (principal.mode == 'master_users' and resource and owns_resource(principal, resource)
                and resource['is_active'] and not resource_deleted(resource) and not resource.get('source_missing_at')):
            return
        raise AuthError('AUTH_RESOURCE_FORBIDDEN', 403)
    if method == 'GET' and re.fullmatch(r'/api/v1/resources/[^/]+(?:/versions|/description)?', path):
        from data_sync_etl.domain.search_access import denied
        container = request.app.state.container
        with container.uow() as repo:
            resource = repo.get('master_learning_resources', path.split('/')[4])
            granted = resource and resource['id'] in principal.resource_ids and owns_resource(principal, resource)
            versions = (repo.find('resource_versions', {'resource_id': resource['id']})
                        if resource and not granted else [])
        if resource and not denied(resource) and resource.get('publication_status') == 'PUBLISHED':
            if (granted or public_metadata_version_ids(container, resource['id'])
                    or any(can_read_public_file(container, row['id']) for row in versions)):
                return
    capability = None
    if path.startswith('/api/v1/master-data/') or path.startswith('/admin/sync/'):
        capability = 'sync'
    elif path.startswith('/api/v1/evaluations/'):
        capability = 'evaluation'
    elif path == '/api/v1/search/model-retry' or re.fullmatch(r'/api/v1/resource-versions/[^/]+/(index|reindex)', path):
        capability = 'index'
    elif re.fullmatch(r'/api/v1/resource-versions/[^/]+/(process|postprocess|ocr-jobs|processing-options|translations/retry)', path):
        capability = 'process'
    if principal.role == 'OPERATOR' and capability in principal.permissions:
        return
    raise AuthError('AUTH_FORBIDDEN', 403)
