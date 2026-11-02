"""Verified request-scoped SOURCE FileResponse, including Range and cancellation."""

import re
import sys
from pathlib import PurePosixPath
from threading import Event

import anyio
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse, Response

from data_sync_etl.domain.core import SourceFileError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.logging import event
from data_sync_etl.security.auth import AuthError


def verified_source_response(resolver, reference, reauthorize, *, version_id, range_header=None, download=False):
    """Serve bounded, hash-verified SOURCE bytes without exposing a provider locator.

    The resolver owns the maximum byte budget and provider capability. This is
    used only after an authenticated processing grant, never by public search.
    """
    headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
               'Accept-Ranges': 'bytes'}
    try:
        reauthorize()
        resolver.require_content_capabilities(reference)
        with resolver.open_verified_stream(reference) as stream:
            stream.seek(0, 2)
            size = stream.tell()
            start, end = 0, size - 1
            status = 200
            if range_header:
                match = re.fullmatch(r'bytes=([0-9]*)-([0-9]*)', range_header) if len(range_header) <= 128 else None
                if not match or not any(match.groups()) or size == 0:
                    reauthorize()
                    return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
                left, right = match.groups()
                if left:
                    start = int(left)
                    end = min(int(right), size - 1) if right else size - 1
                else:
                    length = int(right)
                    if length < 1:
                        reauthorize()
                        return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
                    start = max(0, size - length)
                if start >= size or end < start:
                    reauthorize()
                    return Response(status_code=416, headers={**headers, 'Content-Range': f'bytes */{size}'})
                status = 206
                headers['Content-Range'] = f'bytes {start}-{end}/{size}'
            stream.seek(start)
            content = stream.read(max(0, end - start + 1))
        reauthorize()
    except SourceFileError as exc:
        return source_file_error(exc)
    suffix = PurePosixPath(reference.filename).suffix.lower()
    headers['Content-Disposition'] = f'{"attachment" if download else "inline"}; filename="hoc-lieu-{version_id}{suffix}"'
    return Response(content, status_code=status, media_type=reference.mime, headers=headers)


def source_file_error(error):
    code = error.code
    status = 503 if error.transient or code in {'SOURCE_REFERENCE_DISABLED', 'SOURCE_TEMP_POLICY_REQUIRED'} else 409
    if code in {'FILE_NOT_FOUND', 'SOURCE_VERSION_MISSING', 'DRIVE_FILE_NOT_FOUND',
                'CLOUD_STORAGE_OBJECT_NOT_FOUND'}:
        status = 404
    elif code in {'CLOUD_STORAGE_UNAVAILABLE', 'CLOUD_STORAGE_TRANSIENT',
                  'CLOUD_STORAGE_SECRET_UNAVAILABLE'}:
        status = 503
    elif code in {'CLOUD_STORAGE_ACCESS_DENIED', 'CLOUD_STORAGE_BUCKET_MISMATCH'}:
        status = 403
    elif code in {'PERMISSION_DENIED', 'DRIVE_PERMISSION_DENIED', 'BLOCKED_SOURCE_POLICY'}:
        status = 403
    elif code == 'FILE_TOO_LARGE':
        status = 413
    elif code in {'UNSUPPORTED_SOURCE_FORMAT', 'INVALID_SOURCE_FILE', 'INVALID_PDF', 'PREVIEW_INVALID_FILE', 'PREVIEW_EXTERNAL_CONTENT'}:
        status = 415
    message = {
        'EXTERNAL_SOURCE_CONTENT_DISABLED': 'Nguồn ngoài chỉ cho phép truy cập metadata; ETL không tải nội dung tài liệu.',
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
