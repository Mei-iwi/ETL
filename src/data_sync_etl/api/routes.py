from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Path, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from data_sync_etl.api.resource_schemas import CreateResourceResponse, ResouceListResponse
from data_sync_etl.application.ingest_resource import IngestResourceCommand

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
        '/system/capabilities',
    tags=[TAG_SYSTEM],
    summary='Xem khả năng xử lý của hệ thống'
)
def system_capabilities(request: Request):
    return request.app.state.container.capabilities.describe()


@router.get(
        '/resources',
        tags=[TAG_MASTER],
        summary= 'Lấy danh sách học liệu',
        response_model=ResouceListResponse,
)
def list_resources(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
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


@router.post(
    "/resources",
    tags=[TAG_MASTER],
    status_code=201,
    summary="Tiếp nhận học liệu và Metadata đa định dạng",
    response_model=CreateResourceResponse,
)
def create_resource(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=500)],
    resource_type_code: Annotated[str, Form(min_length=1, max_length=50)],
    provider_id: Annotated[str, Form(min_length=1, max_length=30)],
    provider_name: Annotated[str, Form(min_length=1, max_length=255)],
    provider_type: Annotated[str, Form(max_length=30)] = "INTERNAL",
    resource_type_name_vi: Annotated[str | None, Form(max_length=150)] = None,
    subject_id: Annotated[str | None, Form(max_length=30)] = None,
    grade_level_id: Annotated[str | None, Form(max_length=30)] = None,
    publication_status: Annotated[str, Form(max_length=30)] = "PUBLISHED",
    uploaded_by: Annotated[str | None, Form(max_length=30)] = None,
) -> CreateResourceResponse:
    file_bytes_len = file.size
    if file_bytes_len is None:
        file.file.seek(0, 2)
        file_bytes_len = file.file.tell()
        file.file.seek(0)

    filename = file.filename or "unknown"
    declared_mime = file.content_type or "application/octet-stream"

    cmd = IngestResourceCommand(
        title=title,
        resource_type_code=resource_type_code,
        provider_id=provider_id,
        provider_name=provider_name,
        filename=filename,
        file_stream=file.file,
        file_size=file_bytes_len,
        declared_mime=declared_mime,
        provider_type=provider_type,
        resource_type_name_vi=resource_type_name_vi,
        subject_id=subject_id,
        grade_level_id=grade_level_id,
        publication_status=publication_status,
        uploaded_by=uploaded_by,
    )
    result = request.app.state.container.resource_ingest.execute(cmd)
    return CreateResourceResponse(
        id=result.id,
        title=result.title,
        resource_type_code=result.resource_type_code,
        media_category=result.media_category,
        mime_type=result.mime_type,
        file_name=result.file_name,
        file_size=result.file_size,
        source_hash=result.source_hash,
        initial_version_id=result.initial_version_id,
        version_number=result.version_number,
        lifecycle_status=result.lifecycle_status,
        created_at=result.created_at,
    )


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

