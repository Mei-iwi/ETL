from ipaddress import ip_address
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Path, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from data_sync_etl.api.indexing_schemas import (
    ERROR_RESPONSES,
    ChunksResponse,
    IndexRequestResponse,
    IndexStatusResponse,
    ReindexRequest,
    index_error,
)
from data_sync_etl.api.master_sync_schemas import (
    MasterSyncAutoRequest,
    MasterSyncRequest,
    MasterSyncRunResult,
    MasterSyncStatusResponse,
    SourceFileRetryResponse,
    SourceFilesBatchRetryResponse,
    SourceSyncFilesResponse,
)
from data_sync_etl.api.processing_schemas import (
    ProcessingJobStatusResponse,
    ProcessVersionResponse,
    RetryProcessingJobResponse,
)
from data_sync_etl.api.resource_create_schemas import CreateResourceResponse
from data_sync_etl.api.resource_lookup_schemas import (
    GradeLevelListResponse,
    ProviderListResponse,
    ResourceTypeListResponse,
)
from data_sync_etl.api.resource_metadata_schemas import (
    UpdateResourceMetadataRequest,
    UpdateResourceMetadataResponse,
)
from data_sync_etl.api.resource_schemas import ResouceListResponse, ResourceDetailResponse
from data_sync_etl.api.resource_version_schemas import CreateResourceVersionResponse
from data_sync_etl.api.search_schemas import (
    SEARCH_ERROR_RESPONSES,
    ContextResponse,
    SearchRequest,
    SearchResponse,
    search_error,
)
from data_sync_etl.api.subject_schemas import (
    RecommendedSubjectListResponse,
    SubjectListResponse,
)
from data_sync_etl.application.create_resource_version import (
    InvalidResourceVersion,
    ResourceVersionPersistenceError,
)
from data_sync_etl.application.create_resource_version import (
    ResourceVersionNotFound as NewResourceVersionNotFound,
)
from data_sync_etl.application.source_file_import import SourceImportConflict, SourceImportNotFound
from data_sync_etl.application.update_resource_metadata import (
    InvalidResourceMetadata,
    ResourceMetadataNotFound,
    ResourceMetadataPersistenceError,
)
from data_sync_etl.domain.core import (
    AutoSyncBaselineRequired,
    AutoSyncUnavailable,
    DomainError,
    ProcessingJobNotFound,
    ProcessingRetryConflict,
    ResourceCreateError,
    ResourceNotFound,
    ResourceVersionNotFound,
    SyncAlreadyRunning,
)
from data_sync_etl.domain.format_validation import ProcessingFormatError
from data_sync_etl.domain.resource import CreateResourceCommand
from data_sync_etl.domain.resource_metadata import UpdateResourceMetadataCommand
from data_sync_etl.domain.resource_version import CreateResourceVersionCommand

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


def source_file_admin(request):
    container = services(request)
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "Chỉ được quản trị tệp nguồn từ máy cục bộ")
    return container


@router.get("/health", tags=[TAG_SYSTEM])
def health(request: Request):
    try:
        with request.app.state.container.uow() as repo:
            repo.health()
        return {"status": "ok", "database": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable") from None


@router.post('/api/v1/search', tags=[TAG_SEARCH], response_model=SearchResponse,
             responses=SEARCH_ERROR_RESPONSES,
             summary='Tra cứu học liệu theo BoW, Vector hoặc Hybrid')
def search_materials(body: SearchRequest, request: Request):
    try:
        return request.app.state.container.search.search(**body.model_dump())
    except Exception as exc:
        raise search_error(exc) from None


@router.get('/api/v1/chunks/{context_id}/context', tags=[TAG_SEARCH], response_model=ContextResponse,
            responses=SEARCH_ERROR_RESPONSES,
            summary='Xem Chunk đã chọn và các Chunk liền kề trong cùng ContentUnit')
def chunk_context(context_id: Annotated[str, Path(min_length=1, max_length=256)], request: Request,
                  neighbors: Annotated[int, Query(ge=0, le=2)] = 1):
    try:
        return request.app.state.container.search.context(context_id, neighbors)
    except Exception as exc:
        raise search_error(exc) from None

@router.get(
        '/api/v1/system/capabilities',
    tags=[TAG_SYSTEM],
    summary='Xem khả năng xử lý của hệ thống'
)
def system_capabilities(request: Request):
    return request.app.state.container.capabilities.describe()


@router.get(
    "/api/v1/subjects",
    tags=[TAG_MASTER],
    summary="Lấy danh sách môn học",
    response_model=SubjectListResponse,
    response_model_exclude_none=True,
)
def list_subjects(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
    grade_level_id: Annotated[str | None, Query(max_length=30)] = None,
) -> SubjectListResponse:
    try:
        result = request.app.state.container.subject_listing.execute(
            page=page,
            size=size,
            keyword=q,
            grade_level_id=grade_level_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return SubjectListResponse.model_validate(result)


@router.get(
    "/api/v1/subjects/recommended",
    tags=[TAG_MASTER],
    summary="Lấy danh sách môn học đề xuất",
    response_model=RecommendedSubjectListResponse,
)
def recommended_subjects(
    request: Request,
    grade_level_id: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    exclude_ids: Annotated[str | None, Query(max_length=1000)] = None,
) -> RecommendedSubjectListResponse:
    try:
        result = request.app.state.container.subject_recommendation.execute(
            grade_level_id=grade_level_id,
            limit=limit,
            exclude_ids=exclude_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return RecommendedSubjectListResponse.model_validate(result)


@router.get(
    "/api/v1/resource-types",
    tags=[TAG_MASTER],
    summary="Lấy danh mục loại học liệu",
    response_model=ResourceTypeListResponse,
)
def list_resource_types(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> ResourceTypeListResponse:
    result = request.app.state.container.resource_lookups.list_resource_types(
        page=page,
        size=size,
        query=q,
    )
    return ResourceTypeListResponse.model_validate(result)


@router.get(
    "/api/v1/providers",
    tags=[TAG_MASTER],
    summary="Lấy danh mục nhà cung cấp",
    response_model=ProviderListResponse,
)
def list_providers(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> ProviderListResponse:
    result = request.app.state.container.resource_lookups.list_providers(
        page=page,
        size=size,
        query=q,
    )
    return ProviderListResponse.model_validate(result)


@router.get(
    "/api/v1/grade-levels",
    tags=[TAG_MASTER],
    summary="Lấy danh mục khối lớp",
    response_model=GradeLevelListResponse,
)
def list_grade_levels(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> GradeLevelListResponse:
    result = request.app.state.container.resource_lookups.list_grade_levels(
        page=page,
        size=size,
        query=q,
    )
    return GradeLevelListResponse.model_validate(result)


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
def get_resource(id: ResourceId, request: Request) -> ResourceDetailResponse:
    try:
        data = request.app.state.container.resource_detail.run(id)
        return ResourceDetailResponse.model_validate(data)
    except ResourceNotFound:
        raise HTTPException(status_code=404, detail="Resource not found") from None


@router.get(
    "/api/v1/resources/{id}/versions",
    tags=[TAG_MASTER],
    summary="Xem lịch sử phiên bản học liệu",
)
def list_resource_versions(id: ResourceId, request: Request) -> list[dict]:
    try:
        return request.app.state.container.resource_versions.run(id)
    except ResourceNotFound:
        raise HTTPException(status_code=404, detail="Resource not found") from None


@router.get(
    "/api/v1/processing-jobs/{job_id}",
    tags=[TAG_PROCESSING],
    summary="Xem tiến trình xử lý dữ liệu",
    response_model=ProcessingJobStatusResponse,
)
def get_processing_job(
    job_id: Annotated[str, Path(min_length=1, max_length=30)], request: Request
) -> ProcessingJobStatusResponse:
    try:
        result = request.app.state.container.processing_job_detail.run(job_id)
        return ProcessingJobStatusResponse.model_validate(result)
    except ProcessingJobNotFound:
        raise HTTPException(status_code=404, detail="Processing job not found") from None


@router.patch(
    "/api/v1/resources/{id}/metadata",
    tags=[TAG_MASTER],
    summary="Cập nhật Metadata học liệu",
    response_model=UpdateResourceMetadataResponse,
)
def update_resource_metadata(
    id: ResourceId,
    body: UpdateResourceMetadataRequest,
    request: Request,
) -> UpdateResourceMetadataResponse:
    fields = body.model_dump(exclude_unset=True)
    try:
        result = request.app.state.container.update_resource_metadata.execute(
            UpdateResourceMetadataCommand(
                resource_id=id,
                supplied_fields=frozenset(fields),
                **fields,
            )
        )
    except ResourceMetadataNotFound:
        raise HTTPException(404, "Resource not found") from None
    except InvalidResourceMetadata as exc:
        raise HTTPException(
            400,
            detail={
                "code": "RESOURCE_METADATA_INVALID",
                "phase": "metadata",
                "message": str(exc),
            },
        ) from None
    except ResourceMetadataPersistenceError:
        raise HTTPException(
            503,
            detail={
                "code": "PERSISTENCE_ERROR",
                "phase": "metadata",
                "message": "Database rejected resource metadata",
            },
        ) from None
    return UpdateResourceMetadataResponse.model_validate(result)


@router.post(
    "/api/v1/resources",
    tags=[TAG_MASTER],
    summary="Tiếp nhận học liệu và Metadata đã định dạng",
    status_code=201,
    response_model=CreateResourceResponse,
)
def create_resource(
    request: Request,
    file: Annotated[UploadFile, File()],
    title: str = Form(..., min_length=1, max_length=500),
    resource_type_code: str = Form(..., min_length=1, max_length=50),
    provider_id: str = Form(..., min_length=1, max_length=30),
    provider_name: str = Form(..., min_length=1, max_length=255),
    provider_type: str = Form("INTERNAL", max_length=30),
    resource_type_name_vi: str | None = Form(None, max_length=150),
    subject_id: str | None = Form(None, max_length=30),
    grade_level_id: str | None = Form(None, max_length=30),
    publication_status: str = Form("PUBLISHED", max_length=30),
    uploaded_by: str | None = Form(None, max_length=30),
) -> CreateResourceResponse:
    container = services(request)
    try:
        local_client = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local_client = False
    if not local_client:
        raise HTTPException(403, "Resource upload is available only on a local admin interface")
    if uploaded_by is not None and uploaded_by.strip():
        raise HTTPException(400, "uploaded_by requires an authenticated identity")
    if not file.filename:
        raise HTTPException(400, "A filename is required")
    try:
        stream = file.file
        stream.seek(0, 2)
        file_size = stream.tell()
        stream.seek(0)
        result = container.resource_create.execute(
            CreateResourceCommand(
                title=title,
                resource_type_code=resource_type_code,
                provider_id=provider_id,
                provider_name=provider_name,
                filename=file.filename,
                file_stream=stream,
                file_size=file_size,
                declared_mime=file.content_type or "",
                provider_type=provider_type,
                resource_type_name_vi=resource_type_name_vi,
                subject_id=subject_id,
                grade_level_id=grade_level_id,
                publication_status=publication_status,
                uploaded_by=uploaded_by,
            )
        )
    except ResourceCreateError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "phase": exc.phase, "message": exc.message},
        ) from None
    return CreateResourceResponse.model_validate(result.__dict__)


@router.post(
    "/api/v1/resource/{id}/versions",
    tags=[TAG_MASTER],
    summary="Tạo phiên bản file học liệu mới",
    status_code=201,
    response_model=CreateResourceVersionResponse,
)
def create_resource_version(
    id: ResourceId,
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    uploaded_by: str | None = Form(None, max_length=30),
) -> CreateResourceVersionResponse:
    if not file.filename:
        raise HTTPException(400, "A filename is required")
    try:
        stream = file.file
        stream.seek(0, 2)
        file_size = stream.tell()
        stream.seek(0)
        result = request.app.state.container.resource_version_create.execute(
            CreateResourceVersionCommand(
                resource_id=id,
                filename=file.filename,
                file_stream=stream,
                file_size=file_size,
                declared_mime=file.content_type or "",
                uploaded_by=uploaded_by,
            )
        )
    except NewResourceVersionNotFound:
        raise HTTPException(404, "Resource not found") from None
    except InvalidResourceVersion as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": "RESOURCE_VERSION_INVALID",
                "phase": "file_validation",
                "message": str(exc),
            },
        ) from None
    except ResourceVersionPersistenceError:
        raise HTTPException(
            503,
            detail={
                "code": "PERSISTENCE_ERROR",
                "phase": "version_creation",
                "message": "Database rejected the resource version",
            },
        ) from None
    if result.action == "NO_CHANGE":
        response.status_code = 200
    return CreateResourceVersionResponse.model_validate(result.__dict__)

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
    except ProcessingFormatError as exc:
        message = (
            'OCR ảnh cần Tesseract và dữ liệu ngôn ngữ được cấu hình hợp lệ.'
            if exc.code == 'BLOCKED_OCR_RUNTIME' else
            'Định dạng này chưa hỗ trợ trích xuất nội dung.'
        )
        raise HTTPException(400, detail={"code": exc.code, "phase": "processing", "message": message}) from None
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

@router.post(
    "/api/v1/master-data/sync",
    tags=[TAG_MASTER],
    summary="Đồng bộ dữ liệu nguồn vào Master Data",
    response_model=dict[str, MasterSyncRunResult],
)
def sync_master_data(body: MasterSyncRequest, request: Request):
    try:
        return services(request).sync.run(body.stream, body.full)
    except SyncAlreadyRunning:
        raise HTTPException(409, "A Master Data sync is already running") from None


@router.get(
    "/api/v1/master-data/sync/status",
    tags=[TAG_MASTER],
    summary="Xem trạng thái đồng bộ Master Data",
    response_model=MasterSyncStatusResponse,
)
def master_sync_status(request: Request):
    container = services(request)
    return {**container.sync.status(), **container.auto_sync.state(), **container.source_file_import.availability()}


@router.patch(
    "/api/v1/master-data/sync/auto",
    tags=[TAG_MASTER],
    summary="Bật hoặc tắt Auto Incremental Master Data",
    response_model=MasterSyncStatusResponse,
)
def configure_master_sync_auto(body: MasterSyncAutoRequest, request: Request):
    container = services(request)
    try:
        container.auto_sync.configure(body.enabled, body.stream, body.sync_mode)
    except (AutoSyncUnavailable, AutoSyncBaselineRequired) as exc:
        raise HTTPException(409, str(exc)) from None
    return {**container.sync.status(), **container.auto_sync.state(), **container.source_file_import.availability()}


@router.get(
    "/api/v1/master-data/sync/files",
    tags=[TAG_MASTER],
    summary="Xem phiên bản học liệu từ nguồn",
    response_model=SourceSyncFilesResponse,
)
def source_sync_files(
    request: Request,
    after: Annotated[str | None, Query(min_length=1, max_length=30)] = None,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    status: Literal["failed", "failed-retryable", "failed-blocked"] | None = None,
):
    container = source_file_admin(request)
    try:
        result = container.source_file_import.list_files(
            after=after, size=size, failed_only=status is not None,
            retry_eligible=True if status == "failed-retryable" else False if status == "failed-blocked" else None,
        )
    except DomainError:
        raise HTTPException(503, "Không thể đọc phiên bản nguồn; kiểm tra kết nối và quyền đọc") from None
    return SourceSyncFilesResponse.model_validate(result)


@router.post("/api/v1/master-data/sync/files/retry-failed", tags=[TAG_MASTER], status_code=202,
             response_model=SourceFilesBatchRetryResponse)
def retry_failed_source_files(
    request: Request,
    after: Annotated[str | None, Query(min_length=1, max_length=30)] = None,
    size: Annotated[int, Query(ge=1, le=100)] = 100,
):
    container = source_file_admin(request)
    return container.source_file_import.retry_failed(after, size)


@router.post("/api/v1/master-data/sync/files/{file_id}/retry", tags=[TAG_MASTER], status_code=202,
             response_model=SourceFileRetryResponse)
def retry_source_file(file_id: ResourceId, request: Request):
    container = source_file_admin(request)
    try:
        return container.source_file_import.retry(file_id)
    except SourceImportNotFound:
        raise HTTPException(404, "Không tìm thấy tác vụ nhập tệp") from None
    except SourceImportConflict:
        raise HTTPException(409, "Tệp chưa đủ điều kiện thử lại") from None


@router.post("/admin/sync/master", include_in_schema=False)
def sync(body: MasterSyncRequest, request: Request):
    return services(request).sync.run(body.stream, body.full)


@router.post("/api/v1/resource-versions/{id}/index", tags=[TAG_INDEXING],
             status_code=202, response_model=IndexRequestResponse, responses=ERROR_RESPONSES,
             summary="Xếp hàng phân đoạn và lập chỉ mục học liệu")
def index_version(id: ResourceId, request: Request, response: Response):
    container = services(request)
    try:
        result = container.indexing.enqueue(id)
        if result["action"] == "ALREADY_INDEXED":
            response.status_code = 200
        return result
    except Exception as exc:
        raise index_error(exc) from None


@router.post("/admin/resources/{id}/versions/ingest")
def ingest(id: ResourceId, body: IngestInput, request: Request):
    return services(request).ingestion.run(id, body.bucket, body.object_key, body.uploaded_by)


@router.get("/api/v1/resource-versions/{id}/chunks", tags=[TAG_INDEXING],
            response_model=ChunksResponse, responses=ERROR_RESPONSES,
            summary="Xem Chunk và thông tin biểu diễn đã công bố")
def chunk_preview(id: ResourceId, request: Request,
                   offset: Annotated[int, Query(ge=0)] = 0,
                   limit: Annotated[int, Query(ge=1, le=100)] = 10,
                   content_unit_id: Annotated[str | None, Query(min_length=1, max_length=30)] = None):
    try:
        return request.app.state.container.indexing.chunks(
            id, offset=offset, limit=limit, content_unit_id=content_unit_id)
    except Exception as exc:
        raise index_error(exc) from None


@router.post("/admin/resource-versions/{id}/ocr-jobs")
def create_job(id: ResourceId, request: Request, body: JobInput | None = None):
    return services(request).ocr.create_job(id, body.triggered_by if body else None)


@router.get("/api/v1/resource-versions/{id}/index-status", tags=[TAG_INDEXING],
            response_model=IndexStatusResponse, responses=ERROR_RESPONSES,
            summary="Theo dõi trạng thái lập chỉ mục, tách biệt OCR")
def index_status(id: ResourceId, request: Request):
    try:
        return request.app.state.container.indexing.status(id)
    except Exception as exc:
        raise index_error(exc) from None


@router.post("/admin/resource-versions/{id}/postprocess")
def postprocess(id: ResourceId, request: Request):
    return services(request).postprocess.run(id)


@router.post("/api/v1/resource-versions/{id}/reindex", tags=[TAG_INDEXING],
             status_code=202, response_model=IndexRequestResponse, responses=ERROR_RESPONSES,
             summary="Xếp hàng tái lập chỉ mục, giữ nguyên nội dung và lịch sử")
def reindex_version(id: ResourceId, request: Request, body: ReindexRequest | None = None):
    container = services(request)
    try:
        return container.indexing.enqueue(id, force=True)
    except Exception as exc:
        raise index_error(exc) from None


@router.get("/admin/resource-versions/{id}/etl-status")
def status(id: ResourceId, request: Request):
    return services(request).orchestrator.status(id)


@router.get(
    "/api/v1/resource-versions/{id}/content-units",
    tags=[TAG_PROCESSING],
    summary="Xem nội dung đã trích xuất và chuẩn hóa",
)
def list_public_content_units(
    id: ResourceId,
    request: Request,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    search: Annotated[str, Query(max_length=200)] = "",
) -> dict:
    try:
        return request.app.state.container.orchestrator.content_units(
            id, offset=offset, limit=limit, search=search
        )
    except DomainError:
        raise HTTPException(status_code=404, detail="Resource version not found") from None


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
