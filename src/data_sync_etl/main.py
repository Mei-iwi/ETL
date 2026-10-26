import logging
from contextlib import asynccontextmanager
from ipaddress import ip_address

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from data_sync_etl.api.account_routes import router as account_router
from data_sync_etl.api.admin_access import LocalAdminBoundary
from data_sync_etl.api.auth_boundary import AuthBoundary, auth_error
from data_sync_etl.api.auth_routes import router as auth_router
from data_sync_etl.api.profile_routes import router as profile_router
from data_sync_etl.api.reconciliation_routes import router as reconciliation_router
from data_sync_etl.api.resource_deletion_routes import router as resource_deletion_router
from data_sync_etl.api.resource_description_routes import router as resource_description_router
from data_sync_etl.api.resource_visibility import ResourceVisibilityBoundary
from data_sync_etl.api.routes import router
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError
from data_sync_etl.logging import configure, event, flow


class UploadBodyLimitMiddleware:
    def __init__(self, app, max_bytes: int, *, local_admin_settings=None):
        self.app = app
        self.max_bytes = max_bytes
        self.local_admin_settings = local_admin_settings

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not (scope["path"] == "/api/v1/resources"
                    or (scope["path"].startswith("/api/v1/resource/") and scope["path"].endswith("/versions")))
        ):
            await self.app(scope, receive, send)
            return

        if self.local_admin_settings is not None:
            host = (scope.get("client") or (None,))[0]
            try:
                loopback = host is not None and ip_address(host).is_loopback
            except ValueError:
                loopback = False
            authenticated = (getattr(self.local_admin_settings, 'auth_mode', 'legacy_local') != 'legacy_local'
                             and getattr(scope.get('state', {}).get('auth_principal'), 'role', None) == 'ADMIN')
            if not self.local_admin_settings.admin_enabled or not (loopback or authenticated):
                await JSONResponse(status_code=403, content={"detail": "Local admin upload only"})(
                    scope, receive, send
                )
                return

        async def too_large():
            response = JSONResponse(
                status_code=413,
                content={
                    "detail": {
                        "code": "PAYLOAD_TOO_LARGE",
                        "phase": "file_validation",
                        "message": "Upload request exceeds configured limit",
                    }
                },
            )
            await response(scope, receive, send)

        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = 0
        if declared > self.max_bytes:
            await too_large()
            return

        consumed = 0
        exceeded = False
        outgoing = []

        async def limited_receive():
            nonlocal consumed, exceeded
            if exceeded:
                return {"type": "http.request", "body": b"", "more_body": False}
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    exceeded = True
                    return {"type": "http.request", "body": b"", "more_body": False}
            return message

        async def capture_send(message):
            outgoing.append(message)

        await self.app(scope, limited_receive, capture_send)
        if exceeded:
            await too_large()
        else:
            for message in outgoing:
                await send(message)


async def shutdown_runtime(embedding, close=None):
    """Signal before awaiting a worker; cancellation must not skip owned cleanup."""
    try:
        request_stop = getattr(embedding, 'request_stop', None)
        if request_stop is not None:
            request_stop()
        stop = getattr(embedding, 'stop_preload', None)
        if stop is not None:
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(stop)
    finally:
        if close is not None:
            close()


def create_app(container=None):
    @asynccontextmanager
    async def lifespan(app):
        from data_sync_etl.security.auth import create_auth_service, validate_mode
        validate_mode(configured)
        app.state.auth = create_auth_service(configured) if configured.auth_mode != 'legacy_local' else None
        app.state.container = container or Container()
        configure(app.state.container.settings.log_level)
        embedding = getattr(getattr(app.state.container, 'indexing', None), 'embedding', None)
        if app.state.container.settings.semantic_enabled and app.state.container.semantic is not None:
            embedding = app.state.container.semantic.embedding
        preload = getattr(embedding, 'start_preload', None)
        if preload is not None:
            preload()
        if app.state.container.settings.admin_enabled and configured.auth_mode == 'legacy_local':
            logging.getLogger("etl").warning(
                "Admin API enabled without authentication; bind only to 127.0.0.1 for local development"
            )
        try:
            # Load the pinned tokenizer before serving requests. Its first
            # native import/model load must not consume a search deadline.
            from starlette.concurrency import run_in_threadpool

            from data_sync_etl.domain.lexical import analyze

            await run_in_threadpool(analyze, 'Kiểm tra bộ phân tích')
        except Exception as exc:
            logging.getLogger("etl").warning(
                "Lexical analyzer warmup skipped: %s", exc
            )
        try:
            yield
        finally:
            await shutdown_runtime(embedding, app.state.container.close if container is None else None)

    app = FastAPI(title="Data Sync + ETL (local admin/dev)", lifespan=lifespan)
    configured = container.settings if container is not None else Settings()

    from data_sync_etl.security.auth import AuthError

    @app.exception_handler(AuthError)
    async def authentication_error(request, exc):
        return auth_error(exc)

    @app.middleware("http")
    async def correlation(request: Request, call_next):
        with flow() as correlation_id:
            try:
                response = await call_next(request)
            except Exception:
                # Consume failures here: Starlette's outer handler otherwise re-raises
                # them to Uvicorn, which could log sensitive adapter exception text.
                event(stage="api", action="OPERATION_FAILED")
                response = JSONResponse(
                    status_code=500,
                    content={"detail": "Operation failed; inspect stage status"},
                )
            response.headers["X-Correlation-ID"] = correlation_id
            if request.url.path.startswith(("/api/", "/admin/")):
                response.headers["Cache-Control"] = "private, no-store"
            return response

    from data_sync_etl.ports.source_catalogs import SourceCatalogError

    @app.exception_handler(SourceCatalogError)
    async def source_catalog_error(request, exc):
        return JSONResponse(status_code=503, content={"detail": "SOURCE catalog read failed", "code": exc.status})

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return JSONResponse(
            status_code=400,
            content={"detail": "Invalid ETL operation; verify resource, source and job state"},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Return locations/types only; never reflect submitted values or credentials.
        fields = []
        for error in exc.errors():
            location = [str(part) for part in error.get("loc", ()) if part != "body"]
            fields.append(
                {
                    "field": ".".join(location) or "request",
                    "type": error.get("type", "invalid"),
                }
            )
        endpoint = request.scope.get("endpoint")
        if getattr(endpoint, '__name__', '') in {'search_materials', 'chunk_context'}:
            return JSONResponse(status_code=422, content={'detail': {
                'code': 'REQUEST_VALIDATION_ERROR', 'phase': 'search',
                'message': 'Tham số tìm kiếm không hợp lệ.', 'fields': fields,
            }})
        if getattr(endpoint, "__name__", "") in {"index_version", "chunk_preview", "index_status", "reindex_version"}:
            return JSONResponse(status_code=422, content={"detail": {
                "code": "REQUEST_VALIDATION_ERROR", "phase": "indexing",
                "message": "Tham số lập chỉ mục không hợp lệ.", "fields": fields,
            }})
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "code": "REQUEST_VALIDATION_ERROR",
                    "message": "One or more request fields are invalid",
                    "fields": fields,
                }
            },
        )

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse(
            status_code=500, content={"detail": "Operation failed; inspect stage status"}
        )

    app.include_router(router)
    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(profile_router)
    app.include_router(reconciliation_router)
    app.include_router(resource_description_router)
    app.include_router(resource_deletion_router)
    app.add_middleware(
        UploadBodyLimitMiddleware,
        max_bytes=configured.upload_request_max_bytes,
        local_admin_settings=configured,
    )
    app.add_middleware(ResourceVisibilityBoundary)
    app.add_middleware(LocalAdminBoundary, settings=configured)
    app.add_middleware(AuthBoundary, settings=configured)
    return app


app = create_app()
