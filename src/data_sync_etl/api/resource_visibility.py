"""Revalidate resource visibility before response headers and streaming bodies."""
import re

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from data_sync_etl.domain.resource_deletion import resource_deleted


class ResourceVisibilityBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path = scope['path']
        resource_match = re.fullmatch(r'/api/v1/resources/([^/]+)(?:/(versions(?:/ingest)?|metadata|description))?', path)
        version_match = re.fullmatch(r'/api/v1/resource-versions/([^/]+)/[^/]+', path)
        job_match = re.fullmatch(r'/api/v1/processing-jobs/([^/]+)(?:/[^/]+)?', path)
        global_read = ((path == '/api/v1/search' and scope['method'] == 'POST')
                       or (path == '/api/v1/resources' and scope['method'] == 'GET'))
        global_read |= bool(re.fullmatch(r'/api/v1/chunks/[^/]+/context', path))
        if not (global_read or resource_match or version_match or job_match) or (resource_match and scope['method'] == 'DELETE'):
            return await self.app(scope, receive, send)
        request = Request(scope)
        def epoch():
            with request.app.state.container.uow() as repo:
                return repo.resource_visibility_epoch()
        initial_epoch = await run_in_threadpool(epoch) if global_read else None

        def visible():
            concept_guard = scope.get('state', {}).get('concept_visibility_guard')
            if concept_guard is not None:
                try:
                    if not concept_guard():
                        return False
                except Exception:
                    return False
            with request.app.state.container.uow() as repo:
                if global_read:
                    return repo.resource_visibility_epoch() == initial_epoch
                if resource_match:
                    resource_id = resource_match[1]
                else:
                    version_id = version_match[1] if version_match else None
                    if job_match:
                        job = repo.get('ocr_jobs', job_match[1])
                        version_id = job['resource_version_id'] if job else None
                    version = repo.get('resource_versions', version_id) if version_id else None
                    resource_id = version['resource_id'] if version else None
                resource = repo.get('master_learning_resources', resource_id) if resource_id else None
                # Existing handlers keep their missing/inactive error contracts.
                return not resource_deleted(resource)

        async def unavailable():
            code = 'RESOURCE_VISIBILITY_CHANGED' if global_read else 'RESOURCE_NOT_FOUND'
            await JSONResponse({'detail': {'code': code, 'message': 'Resource visibility changed; retry'}},
                status_code=409 if global_read else 404, headers={'Cache-Control': 'private, no-store'})(scope, receive, send)

        if not await run_in_threadpool(visible):
            return await unavailable()
        started, ended = False, False

        async def checked_send(message):
            nonlocal started, ended
            if ended:
                return
            if message['type'] in ('http.response.start', 'http.response.body'):
                if not await run_in_threadpool(visible):
                    ended = True
                    if started:
                        await send({'type': 'http.response.body', 'body': b'', 'more_body': False})
                    else:
                        await unavailable()
                    return
                started |= message['type'] == 'http.response.start'
            await send(message)
        await self.app(scope, receive, checked_send)
