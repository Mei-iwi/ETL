from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter()
ResourceId = Annotated[str, Path(min_length=1, max_length=30)]


class AdminInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class SyncInput(AdminInput):
    stream: Literal["all", "resources", "users", "grades", "subjects"] = "all"
    full: bool = False


class IngestInput(AdminInput):
    bucket: str = Field(min_length=1, max_length=255)
    object_key: str = Field(min_length=1, max_length=500)
    uploaded_by: str | None = Field(default=None, max_length=30)


class JobInput(AdminInput):
    triggered_by: str | None = Field(default=None, max_length=30)


def services(request):
    if not request.app.state.container.settings.admin_enabled:
        raise HTTPException(
            403, "Admin API disabled; enable only on a trusted local development interface"
        )
    return request.app.state.container


@router.get("/health")
def health(request: Request):
    try:
        with request.app.state.container.uow() as repo:
            repo.health()
        return {"status": "ok", "database": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable") from None


@router.post("/admin/sync/master")
def sync(body: SyncInput, request: Request):
    return services(request).sync.run(body.stream, body.full)


@router.post("/admin/resources/{id}/versions/ingest")
def ingest(id: ResourceId, body: IngestInput, request: Request):
    return services(request).ingestion.run(id, body.bucket, body.object_key, body.uploaded_by)


@router.post("/admin/resource-versions/{id}/ocr-jobs")
def create_job(id: ResourceId, request: Request, body: JobInput | None = None):
    return services(request).ocr.create_job(id, body.triggered_by if body else None)


@router.post("/admin/resource-versions/{id}/postprocess")
def postprocess(id: ResourceId, request: Request):
    return services(request).postprocess.run(id)


@router.get("/admin/resource-versions/{id}/etl-status")
def status(id: ResourceId, request: Request):
    return services(request).orchestrator.status(id)


@router.get("/admin/resource-versions/{id}/content-units")
def content_units(
    id: ResourceId,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    search: Annotated[str, Query(max_length=200)] = "",
):
    return services(request).orchestrator.content_units(
        id, offset=offset, limit=limit, search=search
    )
