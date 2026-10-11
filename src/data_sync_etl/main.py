import logging
from contextlib import asynccontextmanager
from ipaddress import ip_address

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
            or scope["path"] != "/api/v1/resources"
        ):
            await self.app(scope, receive, send)
            return

        if self.local_admin_settings is not None:
            host = (scope.get("client") or (None,))[0]
            try:
                loopback = host is not None and ip_address(host).is_loopback
            except ValueError:
                loopback = False
            if not self.local_admin_settings.admin_enabled or not loopback:
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


def create_app(container=None):
    @asynccontextmanager
    async def lifespan(app):
        app.state.container = container or Container()
        configure(app.state.container.settings.log_level)
        if app.state.container.settings.admin_enabled:
            logging.getLogger("etl").warning(
                "Admin API enabled without authentication; bind only to 127.0.0.1 for local development"
            )
        yield
        if container is None:
            if app.state.container.mongo_client is not None:
                app.state.container.mongo_client.close()
            app.state.container.engine.dispose()

    app = FastAPI(title="Data Sync + ETL (local admin/dev)", lifespan=lifespan)
    configured = container.settings if container is not None else Settings()

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
            return response

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
    app.add_middleware(
        UploadBodyLimitMiddleware,
        max_bytes=configured.upload_request_max_bytes,
        local_admin_settings=configured,
    )
    return app


app = create_app()
