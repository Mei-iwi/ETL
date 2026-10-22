"""Verified request-scoped SOURCE FileResponse, including Range and cancellation."""

import sys
from threading import Event

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response

from data_sync_etl.domain.core import SourceFileError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.logging import event
from data_sync_etl.security.auth import AuthError


def source_file_error(error):
    code = error.code
    status = 503 if error.transient or code in {'SOURCE_REFERENCE_DISABLED', 'SOURCE_TEMP_POLICY_REQUIRED'} else 409
    if code in {'FILE_NOT_FOUND', 'SOURCE_VERSION_MISSING', 'DRIVE_FILE_NOT_FOUND'}:
        status = 404
    elif code in {'PERMISSION_DENIED', 'DRIVE_PERMISSION_DENIED', 'BLOCKED_SOURCE_POLICY'}:
        status = 403
    elif code == 'FILE_TOO_LARGE':
        status = 413
    elif code in {'UNSUPPORTED_SOURCE_FORMAT', 'INVALID_SOURCE_FILE', 'INVALID_PDF', 'PREVIEW_INVALID_FILE', 'PREVIEW_EXTERNAL_CONTENT'}:
        status = 415
    message = {
        'SOURCE_REFERENCE_DISABLED': 'Chức năng truy xuất tệp nguồn chưa được bật.',
        'SOURCE_TEMP_POLICY_REQUIRED': 'Truy xuất tệp nguồn đang bị chặn bởi chính sách tệp tạm.',
    }.get(code, 'Không thể truy xuất tệp từ nguồn.')
    event(stage='source_file_access', action='REJECTED', coverage={'error_code': code, 'http_status': status})
    return JSONResponse({'detail': {'code': code, 'message': message}}, status_code=status,
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


class SourceFileResponse(Response):
    def __init__(self, resolver, reference, reauthorize, *, download, preview=None):
        super().__init__(content=b'')
        self.resolver, self.reference, self.reauthorize = resolver, reference, reauthorize
        self.download = download
        self.preview = preview

    async def __call__(self, scope, receive, send):
        cancelled = Event()

        async def disconnected():
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    cancelled.set()
                    return

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(disconnected)
            try:
                await self._serve(scope, receive, send, cancelled)
            finally:
                cancelled.set()
                tasks.cancel_scope.cancel()

    async def _serve(self, scope, receive, send, cancelled):
        context = (self.preview or self.resolver).temporary_path(self.reference, cancelled=cancelled)
        entered = False
        try:
            try:
                with anyio.CancelScope(shield=True):
                    path = await run_in_threadpool(context.__enter__)
                    entered = True
                if cancelled.is_set():
                    return
                await run_in_threadpool(self.reauthorize)
            except SourceFileError as exc:
                if not cancelled.is_set():
                    await source_file_error(exc)(scope, receive, send)
                return
            except SearchError as exc:
                await JSONResponse({'detail': {'code': exc.code, 'message': 'Không thể truy xuất tệp.'}},
                                   status_code=exc.status)(scope, receive, send)
                return
            except AuthError as exc:
                from data_sync_etl.api.auth_boundary import auth_error
                await auth_error(exc)(scope, receive, send)
                return
            mime = {'.pdf': 'application/pdf', '.json': 'application/json'}.get(path.suffix.lower(), self.reference.mime) if self.preview else self.reference.mime
            response = FileResponse(path, media_type=mime,
                                    filename=f'hoc-lieu-{self.reference.target_version_id}{path.suffix}',
                                    content_disposition_type='attachment' if self.download else 'inline',
                                    headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
            # Force bytes through ASGI rather than handing an expiring temp path
            # to a server extension that may open it after cleanup.
            extensions = {k: v for k, v in scope.get('extensions', {}).items() if k != 'http.response.pathsend'}
            await response({**scope, 'extensions': extensions}, receive, send)
        finally:
            if entered:
                # Always clean up even when the client disconnects. Errors after
                # HTTP headers cannot be replaced with another HTTP response.
                error = sys.exc_info()
                with anyio.CancelScope(shield=True):
                    try:
                        await run_in_threadpool(context.__exit__, *error)
                    except SourceFileError:
                        pass
