from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError, bounded_query

Method = Literal['bow', 'vector', 'hybrid']
Identifier = Annotated[str, Field(min_length=1, max_length=30, pattern=r'^\S(?:.*\S)?$')]


class StrictDTO(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', hide_input_in_errors=True)


class SearchFilters(StrictDTO):
    resource_format: Literal['pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt', 'csv', 'png', 'jpg', 'jpeg', 'mp3', 'mp4'] | None = None
    domain: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    subject_id: Identifier | None = None
    grade_level_id: Identifier | None = None
    resource_type_code: Annotated[str, Field(min_length=1, max_length=50)] | None = None
    resource_id: Identifier | None = None
    resource_version_id: Identifier | None = None


class SearchRequest(StrictDTO):
    window_pages: int = Field(default=1, ge=1, le=3)
    query: str = Field(max_length=8000)
    method: Method = 'hybrid'
    filters: SearchFilters = Field(default_factory=SearchFilters)
    page: int = Field(default=1, ge=1, le=10001)
    size: int = Field(default=10, ge=1, le=50)

    @field_validator('query')
    @classmethod
    def query_text(cls, value):
        return bounded_query(value)

    @model_validator(mode='after')
    def offset_bound(self):
        if (self.page - 1) * self.size > 10000:
            raise ValueError('Search offset exceeded')
        return self


class SearchPageRequest(SearchRequest):
    session_id: str = Field(min_length=40, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')


class SearchSessionInfo(StrictDTO):
    id: str = Field(min_length=40, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    ttl_seconds: float = Field(gt=0, le=60)


class SearchItem(StrictDTO):
    source_locator: dict | None = None
    resource_id: str
    resource_title: str
    resource_version_id: str
    version_number: int
    provider_name: str | None
    resource_type_code: str
    bag_id: str | None
    bag_title: str | None
    unit_type: str | None
    representation_mode: Literal['structured', 'fallback', 'metadata']
    matched_keywords: list[str]
    matched_bag_count: int
    score: float
    score_components: dict[str, float]
    snippet: str
    page_from: int | None
    page_to: int | None
    content_unit_id: str | None
    context_id: str | None
    context_url: str | None
    match_origin: Literal['METADATA', 'CONTENT', 'BOTH'] | None = None
    matched_fields: list[str] | None = None
    evidence_kind: Literal['METADATA_FIELD', 'CONTENT_CHUNK'] | None = None
    evidence_text: str | None = None
    resource_format: str | None = None
    can_view_file: bool = False
    context_evidence: Literal['lexical', 'semantic', 'bag'] | None = None
    meaning_variants: list[str] = Field(default_factory=list)
    lexical_evidence: list[dict] = Field(default_factory=list)

    @model_validator(mode='after')
    def metadata_has_no_context(self):
        if self.representation_mode == 'metadata' and (
            self.match_origin != 'METADATA' or any(value is not None for value in
                (self.bag_id, self.content_unit_id, self.context_id, self.context_url))
                or self.matched_bag_count != 0 or self.can_view_file):
            raise ValueError('Metadata cannot contain content locators')
        return self

    @model_serializer(mode='wrap')
    def omit_unused_provenance(self, handler):
        result = handler(self)
        for key in ('match_origin', 'matched_fields', 'evidence_kind', 'evidence_text', 'resource_format', 'source_locator'):
            if getattr(self, key) is None:
                result.pop(key, None)
        return result


class SearchResponse(StrictDTO):
    search_session: SearchSessionInfo | None = None
    page_window: list['SearchResponse'] | None = Field(default=None, max_length=2)
    total_exact: bool | None = None
    session_status: Literal['UNAVAILABLE'] | None = None
    branch_status: dict[str, str] | None = None
    query_expansion: dict | None = None
    search_fingerprint: str | None = None
    readiness: dict[str, int | bool] | None = None
    query: str
    method: Method
    page: int
    size: int
    total: int
    total_pages: int
    items: list[SearchItem]
    ranking_algorithm: str
    representation_unit: Literal['content_bag']
    ready_bag_count: int
    message: Literal['OK', 'NO_READY_INDEX', 'NO_ACCESSIBLE_CONTENT', 'QUERY_NO_KEYWORDS', 'PUBLIC_ACCESS_UNCONFIRMED']
    access_scope: Literal['public', 'local_demo', 'authenticated_admin', 'authenticated_user'] = 'public'
    vector_index: dict | None = None

    @model_serializer(mode='wrap')
    def omit_unused_vector_index(self, handler):
        # Preserve byte-for-byte legacy field presence, including nullable item
        # metadata. response_model_exclude_none would remove required nulls too.
        result = handler(self)
        for name in ('search_session', 'page_window', 'total_exact', 'session_status'):
            if getattr(self, name) is None:
                result.pop(name, None)
        if self.vector_index is None:
            result.pop('vector_index', None)
        if self.branch_status is None:
            result.pop('branch_status', None)
        return result


class ContextChunk(StrictDTO):
    source_locator: dict | None = None
    id: str
    content_unit_id: str
    unit_chunk_sequence: int
    char_start: int
    char_end: int
    page_from: int | None
    page_to: int | None
    text: str
    selected: bool

    @model_serializer(mode='wrap')
    def omit_empty_locator(self, handler):
        result = handler(self)
        if self.source_locator is None:
            result.pop('source_locator', None)
        return result


class Heading(StrictDTO):
    content_unit_id: str
    title: str | None
    unit_type: str


class ContextResponse(StrictDTO):
    access_scope: Literal['public', 'local_demo', 'authenticated_admin', 'authenticated_user'] = 'public'
    resource_id: str
    resource_title: str
    resource_version_id: str
    version_number: int
    provider_name: str | None
    resource_type_code: str
    context_id: str
    content_unit_id: str
    bag_id: str
    heading_path: list[Heading]
    neighbors: int
    chunks: list[ContextChunk]


class SearchErrorDetail(StrictDTO):
    code: str
    phase: Literal['search']
    message: str
    fields: list[dict[str, str]] | None = None


class SearchErrorResponse(StrictDTO):
    detail: SearchErrorDetail


SEARCH_ERROR_RESPONSES = {status: {'model': SearchErrorResponse} for status in (401, 403, 404, 409, 422, 503)}


MESSAGES = {
    'SEMANTIC_MODEL_UNAVAILABLE': 'Mô hình tìm kiếm ngữ nghĩa chưa sẵn sàng. Có thể chọn BoW.',
    'SEMANTIC_SCHEMA_NOT_READY': 'Chỉ mục ngữ nghĩa chưa được cấu hình. Liên hệ quản trị viên.',
    'SEMANTIC_INDEX_NOT_READY': 'Chỉ mục ngữ nghĩa chưa sẵn sàng. Cần hoàn tất lập chỉ mục.',
    'SEMANTIC_INDEX_STALE': 'Nội dung hoặc chỉ mục đã thay đổi. Vui lòng tìm lại.',
    'SEMANTIC_CANDIDATE_LIMIT': 'Có quá nhiều đoạn văn. Hãy thu hẹp bộ lọc tìm kiếm.',
    'SEMANTIC_TOKEN_LIMIT': 'Văn bản vượt giới hạn xử lý của mô hình ngữ nghĩa.',
    'BLOCKED_LEXICAL_ANALYZER': 'Underthesea chưa sẵn sàng. Cần cài đúng phiên bản analyzer.',
    'SEARCH_MODEL_LOADING': 'Đang chuẩn bị tìm kiếm. Có thể chọn BoW.',
    'SEARCH_RUNTIME_BUSY': 'Đang xử lý tìm kiếm khác. Vui lòng thử lại sau.',
    'SEARCH_ACCESS_RESTRICTED': 'Truy cập tìm kiếm nội bộ bị hạn chế.',
    'SEARCH_LIMIT_EXCEEDED': 'Kho dữ liệu vượt giới hạn tìm kiếm. Hãy thu hẹp bộ lọc.',
    'SEARCH_TIMEOUT': 'Tìm kiếm quá thời gian. Hãy thu hẹp bộ lọc.',
    'SEARCH_MODEL_WARMUP_TIMEOUT': 'Chuẩn bị tìm kiếm quá thời gian. Vui lòng thử lại.',
    'SEARCH_INDEX_STALE': 'Chỉ mục vừa thay đổi. Vui lòng tìm lại.',
    'BLOCKED_EMBEDDING_RUNTIME': 'Chưa thể chuẩn bị tìm kiếm. Có thể chọn BoW.',
    'INVALID_EMBEDDING': 'Tìm kiếm theo ngữ nghĩa chưa sẵn sàng.',
    'BLOCKED_INDEX_SCHEMA': 'Schema MongoDB chưa hợp lệ.',
    'SEARCH_SCHEMA_UNAVAILABLE': 'Chưa kết nối được dữ liệu chỉ mục.',
    'CONTEXT_NOT_FOUND': 'Không tìm thấy ngữ cảnh.',
    'CONTEXT_STALE': 'Nội dung đã thay đổi. Vui lòng tìm lại.',
    'FILE_NOT_FOUND': 'Không tìm thấy tệp hoặc chưa có quyền truy cập.',
    'FILE_NOT_READY': 'Tệp chưa sẵn sàng. Kiểm tra xử lý và chỉ mục.',
}


def search_error(exc):
    known = isinstance(exc, (SearchError, IndexingError))
    code = exc.code if known else 'SEARCH_UNAVAILABLE'
    return HTTPException(exc.status if known else 503, detail={
        'code': code, 'phase': 'search',
        'message': MESSAGES.get(code, 'Không thể truy cập dữ liệu tìm kiếm. Vui lòng thử lại.')},
        headers={'Retry-After': '2'} if code == 'SEARCH_MODEL_LOADING' else None)
