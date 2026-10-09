import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from data_sync_etl.api.routes import router
from data_sync_etl.composition import Container
from data_sync_etl.domain.core import DomainError
from data_sync_etl.logging import configure, event, flow


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
            app.state.container.engine.dispose()

    app = FastAPI(title="Data Sync + ETL (local admin/dev)", lifespan=lifespan)

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
        # Do not reflect request input or credentials in validation responses.
        return JSONResponse(status_code=422, content={"detail": "Invalid request fields"})

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        return JSONResponse(
            status_code=500, content={"detail": "Operation failed; inspect stage status"}
        )

    app.include_router(router)
    app.include_router(router, prefix="/api/v1")
    return app


app = create_app()
