"""Local-only Dev boundary. No client role/header/query can mint an internal grant."""

import os
import sys
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


def container_runtime():
    return bool(os.environ.get('container') or os.environ.get('KUBERNETES_SERVICE_HOST')
                or Path('/.dockerenv').is_file() or Path('/run/.containerenv').is_file())


def verified_local_launch():
    """Fail closed for unknown ASGI launchers or proxy-header rewriting.

    Uvicorn's reload child retains sys.argv. Require the actual launch arguments,
    not a client-controlled Host/Origin or an additional deployment flag.
    """
    args = sys.argv
    if container_runtime() or not args or 'uvicorn' not in args[0].lower() or '--no-proxy-headers' not in args:
        return False
    if ('--proxy-headers' in args or '--fd' in args or '--uds' in args
            or any(arg.startswith(('--fd=', '--uds=')) for arg in args)
            or sum(arg == '--host' or arg.startswith('--host=') for arg in args) != 1):
        return False
    try:
        host = args[args.index('--host') + 1]
    except (ValueError, IndexError):
        host = next((arg.partition('=')[2] for arg in args if arg.startswith('--host=')), None)
    return host in ('127.0.0.1', '::1')


def search_access(request):
    settings = request.app.state.container.settings
    if getattr(settings, 'auth_mode', 'legacy_local') != 'legacy_local':
        from data_sync_etl.security.auth import AuthError
        auth = getattr(request.app.state, 'auth', None)
        if auth is None:
            raise AuthError('AUTH_NOT_CONFIGURED', 503)
        principal = auth.principal(request)
        if principal.mode in ('db_users', 'master_users'):
            from data_sync_etl.security.authorization import authorize
            authorize(request, principal)
            source_resolution = (request.method == 'GET'
                                 and request.url.path.startswith('/api/v1/resources/')
                                 and request.url.path.endswith('/source-resolution'))
            source_open = (request.method == 'GET'
                           and request.url.path.startswith('/api/v1/resources/')
                           and request.url.path.endswith('/open'))
            # Authenticated readers may read published resources, including the
            # default UNREVIEWED extraction state. Domain restrictions and live
            # index validation still apply; management grants stay owner-scoped.
            owner_search = (principal.mode == 'master_users' and principal.role != 'ADMIN'
                            and principal.id.startswith('master:') and (
                                (request.url.path in ('/api/v1/search', '/api/v1/search/page') and request.method == 'POST')
                                or (request.method == 'GET' and request.url.path.startswith('/api/v1/chunks/')
                                    and request.url.path.endswith('/context'))
                                or source_resolution or source_open))
            return SearchAccess('authenticated_admin' if principal.role == 'ADMIN' else 'authenticated_user',
                                frozenset(principal.resource_ids), principal.id,
                                principal.id.removeprefix('master:')
                                if principal.mode == 'master_users' and principal.role != 'ADMIN' else None,
                                allow_public=(request.url.path in ('/api/v1/search', '/api/v1/search/page') and request.method == 'POST')
                                or (request.url.path.startswith('/api/v1/chunks/')
                                    and request.url.path.endswith('/context') and request.method == 'GET')
                                or (request.method == 'GET' and request.url.path.startswith('/api/v1/resource-versions/')
                                    and request.url.path.rsplit('/', 1)[-1] in ('file', 'source-info', 'processing-source'))
                                or source_resolution or source_open,
                                allow_shared_unreviewed=True,
                                allow_owned_unreviewed=owner_search or (request.method == 'GET'
                                    and request.url.path.startswith('/api/v1/resource-versions/')
                                    and request.url.path.rsplit('/', 1)[-1] in ('file', 'source-info', 'processing-source'))
                                or source_resolution or source_open)
        return SearchAccess('authenticated_admin' if principal.mode == 'single_admin' else 'local_demo',
                            actor_id=principal.id)
    if not settings.search_internal_dev_enabled:
        return SearchAccess()
    # Fail closed when deployed elsewhere or accessed via a reverse proxy. Launch
    # Dev on --host 127.0.0.1 --no-proxy-headers; this is not production auth.
    peer = request.scope.get('client') or (None,)
    server = request.scope.get('server') or (None,)
    origin = request.headers.get('origin')
    try:
        origin_ok = not origin or loopback(urlsplit(origin).hostname)
        host_ok = loopback(request.url.hostname)
    except ValueError:
        origin_ok = host_ok = False
    forwarded = any(key == 'forwarded' or key.startswith('x-forwarded-') for key in request.headers)
    if (settings.app_environment != 'development' or not settings.admin_enabled
            or not verified_local_launch() or not loopback(peer[0])
            or not loopback(server[0]) or not host_ok or not origin_ok or forwarded):
        raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
    return SearchAccess('local_demo')
