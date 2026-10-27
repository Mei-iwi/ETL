"""Early ASGI authorization; body parsing/streaming cannot create an identity."""

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from data_sync_etl.security.auth import AuthError

AUTH_PUBLIC = {'/api/v1/auth/config', '/api/v1/auth/csrf', '/api/v1/auth/login', '/api/v1/auth/logout'}


def auth_error(error):
    headers = {'Cache-Control': 'private, no-store'}
    if error.retry_after:
        headers['Retry-After'] = str(error.retry_after)
    return JSONResponse({'detail': {'code': error.code, 'message': {
        'AUTH_INVALID_CREDENTIALS': 'Email hoặc mật khẩu không đúng.',
        'AUTH_LOGIN_THROTTLED': 'Thử đăng nhập quá nhiều lần. Vui lòng thử lại sau.',
        'AUTH_LOGIN_REQUIRED': 'Vui lòng đăng nhập.',
        'AUTH_SESSION_EXPIRED': 'Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại.',
        'AUTH_ADMIN_DISABLED': 'Chức năng quản trị đang tắt.',
        'AUTH_MASTER_STALE': 'Dữ liệu xác thực MASTER chưa được đồng bộ đủ mới. Vui lòng kiểm tra luồng đồng bộ users.',
        'AUTH_MASTER_UNAVAILABLE': 'Chưa kết nối được MASTER để xác thực. Vui lòng thử lại sau.',
        'RESOURCE_NOT_FOUND': 'Không tìm thấy học liệu.',
        'RESOURCE_DELETION_CONFLICT': 'Trạng thái học liệu đã thay đổi. Vui lòng tải lại trước khi thực hiện.',
        'RESOURCE_ORIGIN_UNVERIFIED': 'Chưa xác minh được nguồn quản lý học liệu.',
        'RESOURCE_RESTORE_SOURCE_RESTRICTED': 'Chưa thể khôi phục. Vui lòng kiểm tra trạng thái nguồn và xuất bản.',
        'RESOURCE_RESTORE_VERSION_NOT_READY': 'Phiên bản hiện hành chưa đủ điều kiện khôi phục.',
    }.get(error.code, 'Yêu cầu xác thực không được chấp nhận.')}}, status_code=error.status, headers=headers)


class AuthBoundary:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or self.settings.auth_mode == 'legacy_local':
            await self.app(scope, receive, send)
            return
        path = scope['path']
        if not (path.startswith('/api/') or path in ('/admin', '/openapi.json') or path.startswith('/admin/')):
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        auth = getattr(request.app.state, 'auth', None)
        if auth is None:
            await auth_error(AuthError('AUTH_NOT_CONFIGURED', 503))(scope, receive, send)
            return
        unsafe = scope['method'] not in ('GET', 'HEAD', 'OPTIONS')
        try:
            auth.provenance(request)
            principal = None
            if path not in AUTH_PUBLIC:
                principal = await run_in_threadpool(auth.principal, request)
                scope.setdefault('state', {})['auth_principal'] = principal
                if principal.mode in ('db_users', 'master_users'):
                    from data_sync_etl.security.authorization import authorize
                    await run_in_threadpool(authorize, request, principal)
                if (unsafe and path not in ('/api/v1/search', '/api/v1/search/page') and not path.startswith('/api/v1/auth/')
                        and not self.settings.admin_enabled):
                    raise AuthError('AUTH_ADMIN_DISABLED', 403)
            if unsafe:
                auth.provenance(request, unsafe=True)
                auth.check_csrf(request)
        except AuthError as error:
            await auth_error(error)(scope, receive, send)
            return
        if path == '/api/v1/auth/login' and unsafe:
            body = bytearray()
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    return
                body.extend(message.get('body', b''))
                if len(body) > 8192:
                    await auth_error(AuthError('AUTH_REQUEST_TOO_LARGE', 413))(scope, receive, send)
                    return
                if not message.get('more_body'):
                    break
            original_receive = receive
            consumed = False

            async def buffered_receive():
                nonlocal consumed
                if consumed:
                    return await original_receive()
                consumed = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}

            receive = buffered_receive
        started = False
        ended = False

        async def authorized_send(message):
            nonlocal started, ended
            if ended:
                return
            if principal and not scope.get('state', {}).get('auth_change_completed') and message['type'] in ('http.response.start', 'http.response.body'):
                try:
                    await run_in_threadpool(auth.principal, request, touch=False)
                except AuthError as error:
                    if started:
                        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
                    else:
                        await auth_error(error)(scope, receive, send)
                    ended = True
                    return
            if message['type'] == 'http.response.start':
                started = True
                message = {**message, 'headers': [(k, v) for k, v in message['headers'] if k.lower() != b'cache-control']
                           + [(b'cache-control', b'private, no-store')]}
            await send(message)

        await self.app(scope, receive, authorized_send)
