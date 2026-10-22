"""Explicit route capabilities, with independent resource grants for non-admins."""

import re

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
        container.search.file_version(version_id, access=SearchAccess('authenticated_user', allow_public=True))
    except SearchError:
        return False
    return True


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
    if ((path == '/api/v1/search' and method == 'POST')
            or (path.startswith('/api/v1/chunks/') and path.endswith('/context') and method == 'GET')):
        return  # SearchAccess verifies explicit resource grants and eligibility.
    version = re.fullmatch(r'/api/v1/resource-versions/([^/]+)/(file|source-info|chunks|content-units|index-status|etl-status|process|postprocess|index|reindex|ocr-jobs)', path)
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
            if not (method == 'GET' and version[2] in ('file', 'source-info', 'index-status')
                    and can_read_public_file(request.app.state.container, row['id'])):
                raise AuthError('AUTH_RESOURCE_FORBIDDEN', 403)
        if method == 'GET':
            return  # Domain SearchAccess still checks visibility, review and ownership.
    if method == 'GET' and re.fullmatch(r'/api/v1/resources/[^/]+(?:/versions)?', path):
        from data_sync_etl.domain.search_access import denied
        container = request.app.state.container
        with container.uow() as repo:
            resource = repo.get('master_learning_resources', path.split('/')[4])
            granted = resource and resource['id'] in principal.resource_ids and owns_resource(principal, resource)
            versions = (repo.find('resource_versions', {'resource_id': resource['id']})
                        if resource and not granted else [])
        if resource and not denied(resource) and resource.get('publication_status') == 'PUBLISHED':
            if granted or any(can_read_public_file(container, row['id']) for row in versions):
                return
    capability = None
    if path.startswith('/api/v1/master-data/') or path.startswith('/admin/sync/'):
        capability = 'sync'
    elif path.startswith('/api/v1/evaluations/'):
        capability = 'evaluation'
    elif path == '/api/v1/search/model-retry' or re.fullmatch(r'/api/v1/resource-versions/[^/]+/(index|reindex)', path):
        capability = 'index'
    elif re.fullmatch(r'/api/v1/resource-versions/[^/]+/(process|postprocess|ocr-jobs)', path):
        capability = 'process'
    if principal.role == 'OPERATOR' and capability in principal.permissions:
        return
    raise AuthError('AUTH_FORBIDDEN', 403)
