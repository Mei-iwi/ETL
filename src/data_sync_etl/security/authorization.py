"""Explicit route capabilities, with independent resource grants for non-admins."""

import re

from data_sync_etl.security.auth import AuthError


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
            if not row or row['resource_id'] not in principal.resource_ids:
                raise AuthError('AUTH_RESOURCE_FORBIDDEN', 403)
        if method == 'GET':
            return  # Domain SearchAccess still checks visibility, review and ownership.
    if method == 'GET' and re.fullmatch(r'/api/v1/resources/[^/]+(?:/versions)?', path):
        if path.split('/')[4] in principal.resource_ids:
            from data_sync_etl.domain.search_access import denied
            with request.app.state.container.uow() as repo:
                resource = repo.get('master_learning_resources', path.split('/')[4])
            if resource and not denied(resource) and resource.get('publication_status') == 'PUBLISHED':
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
