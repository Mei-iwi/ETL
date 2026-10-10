from data_sync_etl.domain.core import ResourceNotFound
from data_sync_etl.api.resource_schemas import ResourceDetailResponse
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from data_sync_etl.api.processing_schemas import ProcessVersionResponse, RetryProcessingJobResponse
from data_sync_etl.api.resource_schemas import ResouceListResponse
from data_sync_etl.domain.core import (
    ProcessingJobNotFound,
    ProcessingRetryConflict,
    ResourceVersionNotFound,
)

#Swagger Tags
TAG_SYSTEM: str = 'System'
TAG_MASTER: str = 'Learning Resource Ingestion and Management'
TAG_PROCESSING: str = 'Multimedia Data Processing and Normalization'
TAG_INDEXING: str = 'Data Chunking and Representation'
TAG_SEARCH: str = 'Learning Resource Retrieval and Search'
TAG_EVALUATION: str = 'Experimentation and Evaluation'
#Router
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


@router.get("/health", tags=[TAG_SYSTEM])
def health(request: Request):
    try:
        with request.app.state.container.uow() as repo:
            repo.health()
        return {"status": "ok", "database": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable") from None

@router.get(
        '/api/v1/system/capabilities',
    tags=[TAG_SYSTEM],
    summary='Xem khả năng xử lý của hệ thống'
)
def system_capabilities(request: Request):
    return request.app.state.container.capabilities.describe()


@router.get(
        '/api/v1/resources',
        tags=[TAG_MASTER],
        summary= 'Lấy danh sách học liệu',
        response_model=ResouceListResponse,
)

def list_resources(
    request: Request,
    page: Annotated[int, Query(ge=1)],
    size: Annotated[int, Query(ge=1, le=100)],
    q: Annotated[str, Query(max_length=200)],
    subject_id: Annotated[
        str | None, Query(max_length=30)
    ] = None,
    grade_level_id: Annotated[
        str | None, Query(max_length=30)
    ] = None,
    resource_type_code: Annotated[
        str | None, Query(max_length=50)
    ] = None
) -> ResouceListResponse:
    result = (
        request.app.state.container.resource_listing.run(
            page=page,
            size=size,
            q=q,
            subject_id=subject_id,
            grade_level_id=grade_level_id,
            resource_type_code=resource_type_code,
        )
    )
    return ResouceListResponse.model_validate(result)
@router.get(
    "/api/v1/resources/{id}",
    tags=[TAG_MASTER],
    summary="Xem chi tiết học liệu",
    response_model=ResourceDetailResponse,
)
def get_resource(
    id: ResourceId,
    request: Request,
) -> ResourceDetailResponse:
    try:
        data = request.app.state.container.resource_detail.run(id)
        return ResourceDetailResponse.model_validate(data)
    except ResourceNotFound:
        raise HTTPException(status_code=404, detail="Resource not found") from None

@router.post(
        '/api/v1/resource-versions/{id}/process',
        tags=[TAG_PROCESSING],
        summary='Bắt đầu tiến trình phiên bản học liệu',
        status_code=202,
        response_model=ProcessVersionResponse,
)
def process_resource_version(
    id: ResourceId,
    request: Request,
    response: Response,
) -> ProcessVersionResponse:

    container = services(request)
    try:
        result = container.process_version.run(id)
    except ResourceVersionNotFound:
        raise HTTPException(404, 'Resource version not found') from None

    if result['processing_status'] in ('CONTENT_READY', 'OCR_FAILED', 'FINALIZATION_FAILED'):
        response.status_code = 200

    return ProcessVersionResponse.model_validate(result)


@router.post(
    "/api/v1/processing-jobs/{job_id}/retry",
    tags=[TAG_PROCESSING],
    summary="Yêu cầu xử lý lại Processing Job thất bại",
    status_code=202,
    response_model=RetryProcessingJobResponse,
)
def retry_processing_job(
    job_id: Annotated[str, Path(min_length=1, max_length=30)],
    request: Request,
    response: Response,
) -> RetryProcessingJobResponse:
    container = services(request)
    try:
        result = container.retry_processing_job.run(job_id)
    except ProcessingJobNotFound:
        raise HTTPException(404, "Processing job not found") from None
    except ProcessingRetryConflict:
        raise HTTPException(409, "Processing job cannot be retried now") from None
    if result["action"] not in ("OCR_REQUEUED", "FINALIZATION_REQUEUED"):
        response.status_code = 200
    return RetryProcessingJobResponse.model_validate(result)

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

