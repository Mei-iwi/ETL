"""Verified local-only admin boundary, not a production identity provider."""

from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

from data_sync_etl.api import search_access as policy


def local_admin_allowed(request):
    container = getattr(request.app.state, "container", None)
    if container is None:
        return False
    settings = container.settings
    peer = (request.scope.get("client") or (None,))[0]
    server = (request.scope.get("server") or (None,))[0]
    origin = request.headers.get("origin")
    try:
        host_ok = policy.loopback(request.url.hostname)
        origin_ok = not origin or policy.loopback(urlsplit(origin).hostname)
    except ValueError:
        return False
    forwarded = any(k == "forwarded" or k.startswith("x-forwarded-") for k in request.headers)
    return (
        settings.app_environment == "development"
        and settings.admin_enabled
        and policy.verified_local_launch()
        and policy.loopback(peer)
        and policy.loopback(server)
        and host_ok
        and origin_ok
        and not forwarded
    )


def require_local_admin(request):
    if not local_admin_allowed(request):
        raise HTTPException(403, "Verified local Development admin interface required")
    return request.app.state.container


def protected(path, method):
    return (
        path.startswith("/admin/")
        or (
            path.startswith("/api/")
            and method not in {"GET", "HEAD", "OPTIONS"}
            and path != "/api/v1/search"
        )
        or path.startswith("/api/v1/master-data/")
        or path == "/api/v1/resources"
        or path.startswith("/api/v1/resources/")
        or path.startswith("/api/v1/processing-jobs/")
        or path.endswith(("/index-status", "/etl-status"))
        or path.endswith(("/chunks", "/content-units"))
    )


class LocalAdminBoundary:
    def __init__(self, app, settings):
        self.app, self.settings = app, settings

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and protected(scope["path"], scope["method"]):
            request = Request(scope)
            # Lifespan container is set before requests. Never read request body.
            if not local_admin_allowed(request):
                await JSONResponse(
                    status_code=403,
                    content={"detail": "Local admin access denied"},
                    headers={"Cache-Control": "private, no-store"},
                )(scope, receive, send)
                return

        async def private_send(message):
            if message["type"] == "http.response.start" and protected(
                scope["path"], scope["method"]
            ):
                message = {
                    **message,
                    "headers": [
                        (k, v) for k, v in message["headers"] if k.lower() != b"cache-control"
                    ]
                    + [(b"cache-control", b"private, no-store")],
                }
            await send(message)

        await self.app(scope, receive, private_send if scope["type"] == "http" else send)
