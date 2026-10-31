from ipaddress import ip_address
from pathlib import PurePosixPath
from typing import Annotated, Literal

import anyio
from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)

from data_sync_etl.api.admin_access import require_local_admin
from data_sync_etl.api.evaluation_schemas import (
    DatasetRequest,
    DatasetResponse,
    RunDetailResponse,
    RunListResponse,
    RunRequest,
    RunResponse,
    evaluation_error,
)
from data_sync_etl.api.indexing_schemas import (
    ERROR_RESPONSES,
    ChunksResponse,
    IndexRequestResponse,
    IndexStatusResponse,
    ReindexRequest,
    index_error,
)
from data_sync_etl.api.master_sync_schemas import (
    SYNC_ERRORS,
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
from data_sync_etl.api.search_access import search_access
from data_sync_etl.api.search_schemas import (
    SEARCH_ERROR_RESPONSES,
    ContextResponse,
    SearchPageRequest,
    SearchRequest,
    SearchResponse,
    search_error,
)
from data_sync_etl.api.source_file_response import SourceFileResponse, source_file_error
from data_sync_etl.api.source_resolution_schemas import SourceOpenResponse, SourceResolutionResponse
from data_sync_etl.api.source_writeback_schemas import (
    SourceWriteBackRequest,
    SourceWriteBackResponse,
    SourceWriteBackStatusResponse,
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
from data_sync_etl.application.source_metadata_writeback import SourceWriteBackError
from data_sync_etl.application.source_resolution import SourceResolutionError
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
    SourceFileError,
    SyncAlreadyRunning,
)
from data_sync_etl.domain.format_validation import ProcessingFormatError
from data_sync_etl.domain.resource import CreateResourceCommand
from data_sync_etl.domain.resource_metadata import (
    ResourceMetadataOwnershipError,
    UpdateResourceMetadataCommand,
)
from data_sync_etl.domain.resource_version import CreateResourceVersionCommand
from data_sync_etl.domain.search import SearchError
from data_sync_etl.logging import event

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
    return require_local_admin(request)


def source_file_admin(request):
    container = services(request)
    if container.settings.auth_mode != 'legacy_local':
        return container
    try:
        local = request.client is not None and ip_address(request.client.host).is_loopback
    except ValueError:
        local = False
    if not local:
        raise HTTPException(403, "Chỉ được quản trị tệp nguồn từ máy cục bộ")
    return container


@router.get("/health", tags=[TAG_SYSTEM])
def health(request: Request):
    if request.app.state.container.settings.auth_mode != 'legacy_local':
        return {'status': 'ok'}
    try:
        with request.app.state.container.uow() as repo:
            repo.health()
        return {"status": "ok", "database": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable") from None


@router.get('/api/v1/system/health', tags=[TAG_SYSTEM])
def system_health(request: Request, response: Response):
    container = services(request)
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        with container.uow() as repo:
            repo.health()
    except Exception:
        return JSONResponse({'status': 'ok', 'database': 'unavailable'}, status_code=503,
                            headers={'Cache-Control': 'private, no-store'})
    return {'status': 'ok', 'database': 'ok'}


@router.get('/api/v1/search/model-status', tags=[TAG_SEARCH])
async def search_model_status(request: Request, response: Response):
    # This process alone reports its resident model, never another worker's.
    response.headers['Cache-Control'] = 'private, no-store'
    embedding = request.app.state.container.indexing.embedding
    if request.app.state.container.settings.semantic_enabled and request.app.state.container.semantic is not None:
        embedding = request.app.state.container.semantic.embedding
    status = getattr(embedding, 'search_status', None)
    return status() if status is not None else {'state': 'FAILED', 'ready': False}


@router.post('/api/v1/search/model-retry', tags=[TAG_SEARCH])
async def retry_search_model(request: Request, response: Response):
    try:
        if not search_access(request).internal:
            raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
        embedding = request.app.state.container.indexing.embedding
        if request.app.state.container.settings.semantic_enabled and request.app.state.container.semantic is not None:
            embedding = request.app.state.container.semantic.embedding
        retry = getattr(embedding, 'retry_preload', None)
        if retry is None or not retry():
            raise SearchError('BLOCKED_EMBEDDING_RUNTIME')
        response.headers['Cache-Control'] = 'private, no-store'
        return embedding.search_status()
    except HTTPException:
        raise
    except Exception as exc:
        raise search_error(exc) from None


@router.post('/api/v1/search', tags=[TAG_SEARCH], response_model=SearchResponse,
             responses=SEARCH_ERROR_RESPONSES,
             summary='Tra cứu học liệu theo BoW, Vector hoặc Hybrid')
async def search_materials(body: SearchRequest, request: Request):
    call = None
    try:
        # MASTER authorization can perform blocking DB reads. Keep it off the
        # event loop; runtime capacity is still acquired only after authorization.
        access = await run_in_threadpool(search_access, request)
        service = request.app.state.container.search
        def reauthorize():
            # A request-local trusted policy lookup, never a role/user from JSON.
            # Revoked or switched grants cannot survive the inference queue.
            if search_access(request) != access:
                raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
        call = service.new_http_call(body.method, access=access, authorize=reauthorize)
        completed, outcome = False, None

        def execute():
            try:
                with service.runtime_admission(body.method, access=access, call=call):
                    call.reauthorize()
                    payload = body.model_dump(exclude={'window_pages'})
                    container = request.app.state.container
                    enabled = getattr(container.settings, 'search_result_sessions_enabled', False)
                    gateway = getattr(container, 'search_sessions', None) if enabled else None
                    if gateway is not None:
                        criteria = {key: value for key, value in payload.items() if key != 'page'}
                        try:
                            result = gateway.read(criteria, access, page=body.page, size=body.size,
                                                  window_pages=body.window_pages)
                            request.scope.setdefault('state', {})['search_session_guard'] = gateway.visibility_guard(
                                result['search_session']['id'], criteria, access)
                            return True, result
                        except SearchError as exc:
                            if exc.status != 409:
                                raise
                    result = service.search_http(**payload, access=access)
                    if enabled:
                        result = {**result, 'session_status': 'UNAVAILABLE'}
                    return True, result
            except Exception as exc:
                return False, exc  # Preserve the API error, not an ExceptionGroup.

        async def disconnected(group):
            while True:
                if await request.is_disconnected():
                    call.cancel('disconnect')
                    group.cancel_scope.cancel()
                    return
                await anyio.sleep(.025)

        try:
            # Abandon WAITING for blocking I/O/model on timeout, not its lease.
            # The bounded worker and inference permits are released by execute
            # only after the actual work exits. No detached model threads added.
            with anyio.fail_after(call.remaining()):
                async with anyio.create_task_group() as group:
                    group.start_soon(disconnected, group)
                    outcome = await anyio.to_thread.run_sync(execute, abandon_on_cancel=True)
                    completed = True
                    group.cancel_scope.cancel()
        except TimeoutError:
            call.cancel('deadline')
            raise SearchError('SEARCH_TIMEOUT') from None
        if not completed:
            raise SearchError('SEARCH_TIMEOUT')
        succeeded, value = outcome
        if not succeeded:
            raise value
        call.check()
        await run_in_threadpool(call.reauthorize)
        if call.concept_guard is not None:
            request.scope.setdefault('state', {})['concept_visibility_guard'] = call.concept_guard
        return value
    except HTTPException:
        raise
    except Exception as exc:
        raise search_error(exc) from None
    finally:
        if call is not None:
            call.abandon()


@router.post('/api/v1/search/page', tags=[TAG_SEARCH], response_model=SearchResponse,
             responses=SEARCH_ERROR_RESPONSES)
async def search_session_page(body: SearchPageRequest, request: Request):
    call = None
    try:
        access = await run_in_threadpool(search_access, request)
        container = request.app.state.container
        gateway = getattr(container, 'search_sessions', None)
        if not getattr(container.settings, 'search_result_sessions_enabled', False) or gateway is None:
            raise SearchError('SEARCH_SESSION_UNAVAILABLE', 409)
        criteria = body.model_dump(exclude={'page', 'session_id', 'window_pages'})
        def reauthorize():
            if search_access(request) != access:
                raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
        service = container.search
        call = service.new_http_call('bow', access=access, authorize=reauthorize)
        def execute():
            with service.runtime_admission('bow', access=access, call=call):
                call.reauthorize()
                return gateway.read(criteria, access, page=body.page, size=body.size,
                    token=body.session_id, window_pages=body.window_pages)
        with anyio.fail_after(min(10, call.remaining())):
            result = await anyio.to_thread.run_sync(execute, abandon_on_cancel=True)
        request.scope.setdefault('state', {})['search_session_guard'] = gateway.visibility_guard(
            body.session_id, criteria, access)
        if await run_in_threadpool(search_access, request) != access:
            raise SearchError('SEARCH_ACCESS_RESTRICTED', 403)
        return result
    except HTTPException:
        raise
    except TimeoutError:
        if call is not None:
            call.cancel('deadline')
        raise search_error(SearchError('SEARCH_TIMEOUT')) from None
    except Exception as exc:
        raise search_error(exc) from None
    finally:
        if call is not None:
            call.abandon()


def evaluation_services(request):
    access = search_access(request)
    container = request.app.state.container
    container.evaluations.authorized(access)
    return container.evaluations, access


@router.post('/api/v1/evaluations/datasets', tags=[TAG_EVALUATION], status_code=201, response_model=DatasetResponse)
def create_evaluation_dataset(body: DatasetRequest, request: Request, response: Response):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        service, access = evaluation_services(request)
        return service.create_dataset(body.model_dump(), access)
    except Exception as exc:
        raise evaluation_error(exc) from None


@router.post('/api/v1/evaluations/runs', tags=[TAG_EVALUATION], status_code=201, response_model=RunResponse)
def run_evaluation(body: RunRequest, request: Request, response: Response):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        service, access = evaluation_services(request)
        if 'application/x-ndjson' in request.headers.get('accept', '').lower():
            from data_sync_etl.domain.evaluation import canonical

            events = service.run_events(body.model_dump(), access)
            try:
                first = next(events)  # Validate scope/dataset/corpus before sending HTTP headers.
            except BaseException:
                events.close()
                raise

            def frame(event):
                if event['type'] == 'result':
                    run = event['run']
                    event = {key: value for key, value in event.items() if key != 'run'} | {
                        'result': {'run_id': run['id'], 'status': run['status'], 'dataset_id': run['dataset_id'],
                                   'warnings': run['warnings'], 'finished_at': run['finished_at']}}
                return canonical(event) + '\n'

            async def stream():
                try:
                    yield frame(first)
                    while (event := await run_in_threadpool(next, events, None)) is not None:
                        yield frame(event)
                except Exception as exc:
                    error = evaluation_error(exc)
                    yield canonical({'type': 'error', 'status': error.status_code, 'detail': error.detail}) + '\n'
                finally:
                    with anyio.CancelScope(shield=True):
                        await run_in_threadpool(events.close)

            return StreamingResponse(stream(), status_code=201, media_type='application/x-ndjson',
                                     headers={'Cache-Control': 'private, no-store', 'X-Accel-Buffering': 'no'})
        run = service.run(body.model_dump(), access)
        return {'run_id': run['id'], 'status': run['status'], 'dataset_id': run['dataset_id'],
                'warnings': run['warnings'], 'finished_at': run['finished_at']}
    except Exception as exc:
        raise evaluation_error(exc) from None


@router.get('/api/v1/evaluations/runs', tags=[TAG_EVALUATION], response_model=RunListResponse)
def list_evaluation_runs(request: Request, response: Response,
                         page: int = Query(default=1, ge=1), size: int = Query(default=7, ge=1, le=50)):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        service, access = evaluation_services(request)
        return service.list_runs(access, page, size)
    except Exception as exc:
        raise evaluation_error(exc) from None


@router.get('/api/v1/evaluations/runs/{id}', tags=[TAG_EVALUATION], response_model=RunDetailResponse)
def get_evaluation_run(id: str, request: Request, response: Response,
                       offset: Annotated[int, Query(ge=0, le=300)] = 0,
                       limit: Annotated[int, Query(ge=1, le=50)] = 20,
                       include_observations: bool = True):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        service, access = evaluation_services(request)
        run = service.get_run(id, access)
        rows = run['observations']
        return {**{key: value for key, value in run.items() if key != 'auth_user_id'},
                'observations': rows[offset:offset + limit] if include_observations else [],
                'pagination': {'offset': offset, 'limit': limit, 'total': len(rows)}}
    except Exception as exc:
        raise evaluation_error(exc) from None


@router.get('/api/v1/evaluations/runs/{id}/report', tags=[TAG_EVALUATION])
def export_evaluation_report(id: str, request: Request, response: Response, format: Literal['csv', 'json'] = 'csv'):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        service, access = evaluation_services(request)
        report = service.report(id, access, format)
        if format == 'json':
            return report
        return Response(report, media_type='text/csv; charset=utf-8', headers={
            'Content-Disposition': f'attachment; filename="evaluation-{id}.csv"',
            'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    except Exception as exc:
        raise evaluation_error(exc) from None


@router.get('/api/v1/chunks/{context_id}/context', tags=[TAG_SEARCH], response_model=ContextResponse,
            responses=SEARCH_ERROR_RESPONSES,
            summary='Xem Chunk đã chọn và các Chunk liền kề trong cùng ContentUnit')
def chunk_context(context_id: Annotated[str, Path(min_length=1, max_length=256)], request: Request,
                  neighbors: Annotated[int, Query(ge=0, le=2)] = 1):
    try:
        return request.app.state.container.search.context(context_id, neighbors, access=search_access(request))
    except HTTPException:
        raise
    except Exception as exc:
        raise search_error(exc) from None

def source_view_response(container, version_id, access, *, embedded=False):
    """Open the exact SOURCE viewer; original bytes stay with its provider."""
    with container.uow() as repo:
        version = repo.get('resource_versions', version_id)
        resource = repo.get('master_learning_resources', version['resource_id']) if version else None
    if not resource or resource.get('source_origin') != 'POSTGRESQL':
        return None
    if not access.internal:
        raise HTTPException(404, 'File not found')
    try:
        opened = container.source_open.resolve(resource['id'], access, version_id)
    except SourceResolutionError as exc:
        event(stage='source_file_access', action='REJECTED',
              coverage={'error_code': exc.code, 'http_status': exc.status})
        message = ('Chức năng truy xuất tệp nguồn chưa được bật.'
                   if exc.code == 'SOURCE_REFERENCE_DISABLED'
                   else 'Không thể mở tệp nguồn.')
        raise HTTPException(exc.status, detail={'code': exc.code, 'message': message}) from None
    if opened['status'] != 'EXTERNAL_VIEWER_AVAILABLE' or len(opened['files']) != 1:
        raise HTTPException(409, detail={'code': opened['status'], 'message': 'Kho nguồn chưa cung cấp link xem hợp lệ.'})
    file = opened['files'][0]
    url = (file.get('embedded_url') if embedded else None) or file['viewer_url']
    return RedirectResponse(url, status_code=307, headers={'Cache-Control': 'private, no-store'})


@router.get('/api/v1/resource-versions/{id}/file', tags=[TAG_MASTER],
            responses=SEARCH_ERROR_RESPONSES,
            summary='Xem hoặc tải tệp học liệu được phép truy xuất')
def resource_file(id: ResourceId, request: Request, download: bool = False, view: Literal['file', 'text', 'preview'] = 'file'):
    container = request.app.state.container
    access = search_access(request)
    if view != 'text':
        source_response = source_view_response(container, id, access, embedded=view == 'preview')
        if source_response is not None:
            return source_response
    try:
        version = container.search.file_version(id, access=search_access(request))
    except Exception as exc:
        raise search_error(exc) from None
    if view == 'text':
        try:
            reference = container.source_resolver.resolve(id)
            # This is derived OCR text, not a copy of the original SOURCE file.
            version = container.search.file_version(id, access=search_access(request), include_text=True)
        except SourceFileError as exc:
            return source_file_error(exc)
        except Exception as exc:
            raise search_error(exc) from None
        if not isinstance(version.get('preview_text'), str):
            # A SOURCE reference without authorized derived OCR text must not
            # become a server-side KeyError/500.  Original bytes remain
            # provider-owned; report the normal readiness contract instead.
            raise search_error(SearchError('FILE_NOT_READY', 409)) from None
        return PlainTextResponse(version['preview_text'],
                                 headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
    try:
        reference = container.source_resolver.resolve(id)
    except SourceFileError as exc:
        return source_file_error(exc)
    if reference.ownership == 'SOURCE_BACKED':
        return source_file_error(SourceFileError('EXTERNAL_SOURCE_CONTENT_DISABLED'))
    if view == 'preview' and not download:
        return SourceFileResponse(container.source_resolver, reference,
                                  lambda: container.search.file_version(id, access=search_access(request)), download=download,
                                  preview=container.file_previews if view == 'preview' and not download else None)
    suffix = PurePosixPath(version['storage_object_key']).suffix.lower()
    media_types = {'.pdf': 'application/pdf', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                   '.txt': 'text/plain', '.csv': 'text/csv', '.mp3': 'audio/mpeg', '.mp4': 'video/mp4',
                   '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                   '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                   '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation'}
    if suffix not in media_types:
        raise HTTPException(415, 'Không hỗ trợ xem định dạng tệp này.')
    bucket, key = version['storage_bucket'], version['storage_object_key']
    try:
        if not container.storage.exists(bucket, key):
            raise DomainError('File unavailable')
        path = container.storage.get_local_path(bucket, key)
        validator = container.format_registry.find_validator("file" + suffix)
        size = path.stat().st_size
        if size > validator.max_size_bytes:
            raise HTTPException(413, "Tệp vượt giới hạn định dạng.")
        with path.open("rb") as stream:
            validator.validate(suffix, media_types[suffix], stream.read(16), size)
    except HTTPException:
        raise
    except (DomainError, OSError):
        raise HTTPException(404, 'Tệp chưa sẵn sàng hoặc không còn tồn tại.') from None
    except Exception:
        raise HTTPException(502, "Dịch vụ tệp đang gián đoạn.") from None
    return FileResponse(path, media_type=media_types[suffix], filename=f'hoc-lieu-{id}{suffix}',
                        content_disposition_type='attachment' if download else 'inline',
                        headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/api/v1/resource-versions/{id}/source-info', tags=[TAG_MASTER],
            summary='Tham chiếu nguồn được phép, không công khai locator nội bộ')
def resource_source_info(id: ResourceId, request: Request):
    container = request.app.state.container
    source_response = source_view_response(container, id, search_access(request))
    if source_response is not None:
        return {'ownership': 'SOURCE_BACKED', 'provider': 'external', 'availability': 'AVAILABLE',
                'can_open_original': True, 'original_url': source_response.headers['location']}
    try:
        container.search.file_version(id, access=search_access(request))
        reference = container.source_resolver.resolve(id)
        if reference.ownership == 'SOURCE_BACKED':
            return container.source_resolver.display_reference(reference)
        info = container.source_resolver.display_reference(reference)
        container.search.file_version(id, access=search_access(request))
        return info
    except SourceFileError as exc:
        return source_file_error(exc)
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
    ] = None,
    deleted_only: bool = False,
) -> ResouceListResponse:
    principal = getattr(request.state, 'auth_principal', None)
    if deleted_only:
        from data_sync_etl.security.auth import AuthError
        if not request.app.state.container.settings.admin_enabled or principal is None or principal.role != 'ADMIN':
            raise AuthError('AUTH_FORBIDDEN', 403)
    allowed = (frozenset(principal.resource_ids) if principal is not None
               and principal.mode in ('db_users', 'master_users') and principal.role != 'ADMIN' else None)
    result = (
        request.app.state.container.resource_listing.run(
            page=page,
            size=size,
            q=q,
            subject_id=subject_id,
            grade_level_id=grade_level_id,
            resource_type_code=resource_type_code,
            **({'deleted_only': True} if deleted_only else {}),
            **({'allowed_resource_ids': allowed} if allowed is not None else {}),
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
    "/api/v1/resources/{id}/source-resolution",
    tags=[TAG_MASTER],
    response_model=SourceResolutionResponse,
    summary="Resolve học liệu qua MASTER tới metadata SOURCE bằng ID chính xác",
    responses=SEARCH_ERROR_RESPONSES,
)
def source_resolution(id: ResourceId, request: Request,
                      version_id: Annotated[str | None, Query(min_length=1, max_length=30)] = None) -> SourceResolutionResponse:
    access = search_access(request)
    if (access.internal
            and not getattr(request.app.state.container.settings, 'source_reference_enabled', False)):
        raise HTTPException(status_code=503, detail={
            'code': 'SOURCE_REFERENCE_DISABLED',
            'message': 'Chức năng truy xuất tệp nguồn chưa được bật.',
        })
    try:
        result = request.app.state.container.source_resolution.resolve(id, access, version_id)
        return SourceResolutionResponse.model_validate(result)
    except SourceResolutionError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail={'code': exc.code, 'message': 'Resource not found'}) from None
        raise HTTPException(status_code=exc.status,
                            detail={'code': exc.code, 'message': 'Source resolution is restricted'}) from None
    except Exception as exc:
        raise search_error(exc) from None


@router.get(
    "/api/v1/resources/{id}/open",
    tags=[TAG_MASTER],
    response_model=SourceOpenResponse,
    summary="Resolve an exact external SOURCE viewer without downloading the file",
    responses=SEARCH_ERROR_RESPONSES,
)
def source_open(id: ResourceId, request: Request,
                version_id: Annotated[str | None, Query(min_length=1, max_length=30)] = None) -> SourceOpenResponse:
    access = search_access(request)
    try:
        result = request.app.state.container.source_open.resolve(id, access, version_id)
        return SourceOpenResponse.model_validate(result)
    except SourceResolutionError as exc:
        if exc.status == 404:
            raise HTTPException(status_code=404, detail={'code': exc.code, 'message': 'Resource not found'}) from None
        raise HTTPException(status_code=exc.status,
                            detail={'code': exc.code, 'message': 'Source opening is restricted'}) from None
    except Exception as exc:
        raise search_error(exc) from None


@router.get('/api/v1/source-federation/health', tags=[TAG_SYSTEM], summary='Trạng thái từng nguồn đã đăng ký')
def source_federation_health(request: Request):
    access = search_access(request)
    if not access.internal:
        raise HTTPException(status_code=403, detail='Authentication required')
    registry = services(request).source_registry
    sources = []
    for registration in registry.registrations():
        status = registry.health(registration.source_id)
        sources.append({
            'source_id': registration.source_id,
            'backend': registration.backend,
            'namespace': registration.namespace,
            'enabled': registration.enabled,
            'health': status,
        })
    return {'sources': sources}


@router.get(
    "/api/v1/resources/{id}/source-writeback/status",
    tags=[TAG_MASTER],
    response_model=SourceWriteBackStatusResponse,
    summary="Xem trạng thái quyền đồng bộ metadata MASTER về SOURCE",
)
def source_writeback_status(id: ResourceId, request: Request) -> SourceWriteBackStatusResponse:
    container = services(request)
    with container.uow() as repo:
        if repo.get('master_learning_resources', id) is None:
            raise HTTPException(status_code=404, detail='Resource not found')
    return SourceWriteBackStatusResponse.model_validate(container.source_writeback.status())


@router.get(
    "/api/v1/source-writeback/status",
    tags=[TAG_MASTER],
    response_model=SourceWriteBackStatusResponse,
    summary="Xem trạng thái toàn cục của write-back metadata",
)
def source_writeback_global_status(request: Request) -> SourceWriteBackStatusResponse:
    return SourceWriteBackStatusResponse.model_validate(services(request).source_writeback.status())


@router.post(
    "/api/v1/resources/{id}/source-writeback",
    tags=[TAG_MASTER],
    response_model=SourceWriteBackResponse,
    summary="Đề nghị hoặc phê duyệt đồng bộ metadata MASTER về SOURCE",
)
def source_writeback(id: ResourceId, body: SourceWriteBackRequest,
                     request: Request) -> SourceWriteBackResponse:
    container = services(request)
    principal = getattr(request.state, 'auth_principal', None)
    local_admin = container.settings.auth_mode == 'legacy_local'
    actor_id = getattr(principal, 'id', None) or ('local-admin' if local_admin else None)
    role = getattr(principal, 'role', None) or ('ADMIN' if local_admin else None)
    if not actor_id or not role:
        raise HTTPException(403, detail={'code': 'SOURCE_WRITEBACK_FORBIDDEN'})
    owner_id = actor_id.removeprefix('master:') if actor_id.startswith('master:') else None
    fields = body.model_dump(exclude={'expected_source_state_hash', 'idempotency_key', 'approve', 'dry_run'},
                             exclude_none=True)
    try:
        result = container.source_writeback.request(
            id, fields, expected_source_state_hash=body.expected_source_state_hash,
            idempotency_key=body.idempotency_key, actor_id=actor_id, role=role,
            owner_id=owner_id, approve=body.approve, dry_run=body.dry_run,
        )
        return SourceWriteBackResponse.model_validate(result)
    except SourceWriteBackError as exc:
        raise HTTPException(status_code=exc.status,
                            detail={'code': exc.code, 'phase': 'source_writeback',
                                    'message': 'SOURCE write-back request was not applied'}) from None


@router.get(
    "/api/v1/resources/{id}/versions",
    tags=[TAG_MASTER],
    summary="Xem lịch sử phiên bản học liệu",
)
def list_resource_versions(id: ResourceId, request: Request) -> list[dict]:
    try:
        container = request.app.state.container
        principal = getattr(request.state, 'auth_principal', None)
        if container.settings.auth_mode != 'legacy_local' and principal is None:
            from data_sync_etl.security.auth import AuthError
            raise AuthError('AUTH_LOGIN_REQUIRED', 401)
        versions = container.resource_versions.run(id)
        if principal and principal.mode in ('db_users', 'master_users') and principal.role != 'ADMIN':
            from data_sync_etl.security.authorization import can_read_public_file, owns_resource
            with container.uow() as repo:
                resource = repo.get('master_learning_resources', id)
            if id not in principal.resource_ids or not owns_resource(principal, resource):
                from data_sync_etl.security.authorization import public_metadata_version_ids

                metadata_ids = public_metadata_version_ids(container, id)
                versions = [row for row in versions if row['id'] in metadata_ids or can_read_public_file(container, row['id'])]
                readable_ids = {row['id'] for row in versions}
                versions = [{**row, 'parent_version_id': row['parent_version_id']
                             if row['parent_version_id'] in readable_ids else None} for row in versions]
        return versions
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
    container = services(request)
    if "uploaded_by" in body.model_fields_set:
        raise HTTPException(400, "uploaded_by requires an authenticated identity")
    fields = body.model_dump(exclude_unset=True)
    try:
        result = container.update_resource_metadata.execute(
            UpdateResourceMetadataCommand(
                resource_id=id,
                supplied_fields=frozenset(fields),
                **fields,
            )
        )
    except ResourceMetadataNotFound:
        raise HTTPException(404, "Resource not found") from None
    except ResourceMetadataOwnershipError as exc:
        raise HTTPException(409, detail={'code': exc.code, 'phase': 'metadata', 'message': str(exc)}) from None
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
    container = services(request)
    if uploaded_by and uploaded_by.strip():
        raise HTTPException(400, "uploaded_by requires an authenticated identity")
    if not file.filename:
        raise HTTPException(400, "A filename is required")
    try:
        stream = file.file
        stream.seek(0, 2)
        file_size = stream.tell()
        stream.seek(0)
        result = container.resource_version_create.execute(
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
        container = services(request)
        if container.settings.source_federation_enabled:
            return container.sync.run(body.stream, body.full, source_id=body.source_id)
        if body.source_id is not None:
            raise DomainError('SOURCE_FEDERATION_DISABLED')
        return container.sync.run(body.stream, body.full)
    except SyncAlreadyRunning:
        raise HTTPException(409, "A Master Data sync is already running") from None
    except DomainError as exc:
        code = str(exc).split(':', 1)[0]
        if code in SYNC_ERRORS:
            status, message = SYNC_ERRORS[code]
            raise HTTPException(status, detail={'code': code, 'message': message}) from None
        raise


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
    status: Literal["failed", "failed-retryable", "failed-blocked", "integrity-unverified"] | None = None,
    include_total: bool = False,
):
    container = source_file_admin(request)
    from data_sync_etl.adapters.persistence.scheduled_audit import recent_file_evidence
    from data_sync_etl.db.models import (
        MasterLearningResource,
        ReconciliationFileState,
        ResourceVersion,
    )
    evidence_cache = {}
    def evidence_reader(repo, item):
        version_id = item['target_version_id']
        if version_id not in evidence_cache:
            state = repo.session.get(ReconciliationFileState, version_id) if version_id else None
            version = repo.session.get(ResourceVersion, version_id) if state else None
            resource = repo.session.get(MasterLearningResource, version.resource_id) if version else None
            recent = bool(resource and recent_file_evidence(state, version, resource.source_origin, container.settings))
            evidence_cache[version_id] = {'status': state.status if state else 'NOT_VERIFIED',
                'reason_code': state.reason_code if state else None, 'recent_sha256_evidence': recent}
        return evidence_cache[version_id]
    try:
        result = container.source_file_import.list_files(
            after=after, size=size, failed_only=status in ('failed', 'failed-retryable', 'failed-blocked'),
            retry_eligible=True if status == "failed-retryable" else False if status == "failed-blocked" else None,
            include_total=include_total,
            integrity_only=status == 'integrity-unverified', evidence_reader=evidence_reader,
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


@router.post("/api/v1/resources/{id}/versions/ingest", tags=[TAG_MASTER])
def ingest(id: ResourceId, body: IngestInput, request: Request):
    container = services(request)
    if body.uploaded_by is not None:
        raise HTTPException(400, "uploaded_by requires an authenticated identity")
    try:
        return container.ingestion.run(id, body.bucket, body.object_key, local_only=True)
    except NewResourceVersionNotFound:
        raise HTTPException(404, "Resource not found") from None
    except InvalidResourceVersion as exc:
        raise HTTPException(exc.status_code, str(exc)) from None


@router.get("/api/v1/resource-versions/{id}/chunks", tags=[TAG_INDEXING],
            response_model=ChunksResponse, responses=ERROR_RESPONSES,
            summary="Xem Chunk và thông tin biểu diễn đã công bố")
def chunk_preview(id: ResourceId, request: Request,
                   offset: Annotated[int, Query(ge=0)] = 0,
                   limit: Annotated[int, Query(ge=1, le=100)] = 10,
                   content_unit_id: Annotated[str | None, Query(min_length=1, max_length=30)] = None):
    try:
        request.app.state.container.search.authorize_content_version(id, access=search_access(request))
        return request.app.state.container.indexing.chunks(
            id, offset=offset, limit=limit, content_unit_id=content_unit_id)
    except Exception as exc:
        from data_sync_etl.domain.search import SearchError
        if isinstance(exc, SearchError):
            raise search_error(exc) from None
        raise index_error(exc) from None


@router.post("/api/v1/resource-versions/{id}/ocr-jobs", tags=[TAG_PROCESSING])
def create_job(id: ResourceId, request: Request, body: JobInput | None = None):
    services(request)
    if body and body.triggered_by is not None:
        raise HTTPException(400, "triggered_by requires an authenticated identity")
    with services(request).uow() as repo:
        version = repo.get('resource_versions', id)
        resource = repo.get('master_learning_resources', version['resource_id']) if version else None
    if resource and resource.get('source_origin') not in ('LOCAL', None):
        services(request).search.authorize_source_processing_version(id, access=search_access(request))
    return services(request).ocr.create_job(id, body.triggered_by if body else None)


@router.get("/api/v1/resource-versions/{id}/index-status", tags=[TAG_INDEXING],
            response_model=IndexStatusResponse, response_model_exclude_unset=True, responses=ERROR_RESPONSES,
            summary="Theo dõi trạng thái lập chỉ mục, tách biệt OCR")
def index_status(id: ResourceId, request: Request, response: Response, include_bags: bool = True,
                 bag_offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
                 bag_limit: Annotated[int | None, Query(ge=1, le=100)] = None,
                 expected_run_id: Annotated[str | None, Query(min_length=1, max_length=30)] = None,
                 bag_revision: Annotated[str | None, Query(pattern=r'^[a-f0-9]{64}$')] = None):
    response.headers['Cache-Control'] = 'private, no-store'
    try:
        result = request.app.state.container.indexing.status(id)
        # Keep the same live artifact verification; only omit optional wire details.
        if bag_limit is not None:
            from data_sync_etl.domain.indexing import IndexingError, digest

            # Integrity requires the complete artifact. Paging reduces wire JSON,
            # not verification or Mongo/PG I/O.
            bags = sorted(result['bags'], key=lambda bag: bag['id'])
            revision = digest([result['published_run_id'], result['index_status'], bags])
            if (result['index_status'] != 'READY'
                    or expected_run_id is not None and expected_run_id != result['published_run_id']
                    or bag_revision is not None and bag_revision != revision):
                raise IndexingError('INDEX_PAGE_CHANGED', 409)
            return {**result, 'bags': bags[bag_offset:bag_offset + bag_limit] if include_bags else [],
                    'bag_revision': revision, 'bag_pagination': {'offset': bag_offset, 'limit': bag_limit, 'total': len(bags)}}
        return result if include_bags else {**result, 'bags': []}
    except Exception as exc:
        raise index_error(exc) from None


@router.post("/api/v1/resource-versions/{id}/postprocess", tags=[TAG_PROCESSING])
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


@router.get("/api/v1/resource-versions/{id}/etl-status", tags=[TAG_PROCESSING])
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
        request.app.state.container.search.authorize_content_version(id, access=search_access(request))
    except Exception as exc:
        raise search_error(exc) from None
    try:
        return request.app.state.container.orchestrator.content_units(
            id, offset=offset, limit=limit, search=search
        )
    except DomainError:
        raise HTTPException(status_code=404, detail="Resource version not found") from None


class ProcessingOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_language: str | None = Field(default=None, pattern=r'^[a-z]{2,3}$')
    target_language: str = Field(default='vi', pattern=r'^[a-z]{2,3}$')
    audio_track: int | None = Field(default=None, ge=0, le=63)
    subtitle_track: int | None = Field(default=None, ge=0, le=63)
    ocr_enabled: bool = True
    ocr_languages: str | None = Field(default=None, max_length=100, pattern=r'^[a-z]{3}(\+[a-z]{3})*$')
    visual_enabled: bool = False


def bilingual_access(request, version_id):
    container = services(request)
    principal = getattr(request.state, 'auth_principal', None)
    if principal is not None and principal.role != 'ADMIN':
        try:
            container.search.authorize_content_version(version_id, access=search_access(request))
        except Exception as exc:
            raise search_error(exc) from None
    with container.uow() as repo:
        version = repo.get('resource_versions', version_id)
        resource = repo.get('master_learning_resources', version['resource_id']) if version else None
    if not resource or not resource['is_active'] or resource.get('deleted_at'):
        raise HTTPException(404, 'Resource version not found')
    if (resource.get('source_origin') != 'LOCAL'
            and not container.source_resolver.derived_index_authorized(version_id, access=search_access(request))):
        raise HTTPException(403, detail={'code': 'SOURCE_CAPABILITY_BLOCKED',
                                        'message': 'Nguồn chưa cấp quyền đọc nội dung đã xử lý.'})
    return container


@router.put('/api/v1/resource-versions/{id}/processing-options', tags=[TAG_PROCESSING])
def configure_processing(id: ResourceId, body: ProcessingOptions, request: Request):
    container = services(request)
    try:
        return container.bilingual.configure(id, body.model_dump(exclude_unset=True))
    except DomainError as exc:
        raise HTTPException(409, str(exc)) from None


@router.post('/api/v1/resource-versions/{id}/translations/retry', tags=[TAG_PROCESSING], status_code=202)
def retry_translation(id: ResourceId, body: ProcessingOptions, request: Request):
    container = services(request)
    try:
        return container.bilingual.configure(id, body.model_dump(exclude_unset=True), retry_translation=True)
    except DomainError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get('/api/v1/resource-versions/{id}/bilingual', tags=[TAG_PROCESSING])
def bilingual_content(id: ResourceId, request: Request, response: Response, include_items: bool = True,
                      offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
                      limit: Annotated[int | None, Query(ge=1, le=100)] = None,
                      revision: Annotated[str | None, Query(pattern=r'^[a-f0-9]{64}$')] = None):
    response.headers['Cache-Control'] = 'private, no-store'
    container = bilingual_access(request, id)
    try:
        return container.bilingual.view(id, include_items=include_items, offset=offset, limit=limit, revision=revision)
    except DomainError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get('/api/v1/resource-versions/{id}/exports/{format}', tags=[TAG_PROCESSING])
def bilingual_export(id: ResourceId, format: Literal['json', 'txt', 'srt', 'vtt'], request: Request,
                     translated: bool = False):
    from data_sync_etl.application.bilingual import export_segments
    container = bilingual_access(request, id)
    view = container.bilingual.view(id)
    headers = {'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff',
               'Content-Disposition': f'attachment; filename="{id}-{"translated" if translated else "original"}.{format}"'}
    if format == 'json':
        return JSONResponse(view, headers=headers)
    try:
        content = export_segments(view, format, translated)
    except DomainError:
        raise HTTPException(404, 'EXPORT_NOT_AVAILABLE') from None
    return PlainTextResponse(content, headers=headers,
                             media_type='text/vtt' if format == 'vtt' else 'text/plain')


@router.get('/api/v1/resource-versions/{id}/processing-source', tags=[TAG_PROCESSING])
def bilingual_source(id: ResourceId, request: Request, embedded: bool = False):
    source_response = source_view_response(services(request), id, search_access(request), embedded=embedded)
    if source_response is not None:
        return source_response
    container = bilingual_access(request, id)
    try:
        reference = container.source_resolver.resolve(id)
    except SourceFileError as exc:
        return source_file_error(exc)
    if reference.ownership == 'SOURCE_BACKED':
        return source_file_error(SourceFileError('EXTERNAL_SOURCE_CONTENT_DISABLED'))
    return SourceFileResponse(container.source_resolver, reference,
                              lambda: bilingual_access(request, id), download=False)


@router.get('/api/v1/resource-versions/{id}/segments/{segment_id}/frame', tags=[TAG_PROCESSING])
def bilingual_frame(id: ResourceId, segment_id: str, request: Request):
    container = bilingual_access(request, id)
    with container.uow() as repo:
        jobs = repo.find('ocr_jobs', {'resource_version_id': id}, order=('-created_at', '-id'), limit=1)
        for job in jobs:
            for task in repo.find('ocr_page_tasks', {'job_id': job['id']}):
                for result in repo.find('ocr_page_results', {'page_task_id': task['id']}):
                    for segment in (result['result_metadata'] or {}).get('paired_segments', []):
                        if segment['segment_id'] == segment_id and segment.get('frame'):
                            frame = segment['frame']
                            return FileResponse(container.storage.get_local_path(frame['bucket'], frame['object_key']),
                                                media_type='image/png', headers={'Cache-Control': 'private, no-store'})
    raise HTTPException(404, 'Frame not found')
