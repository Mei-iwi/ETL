from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError, normalize_query

Method = Literal['bow', 'vector', 'hybrid']
Identifier = Annotated[str, Field(min_length=1, max_length=30, pattern=r'^\S(?:.*\S)?$')]


class StrictDTO(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', hide_input_in_errors=True)


class SearchFilters(StrictDTO):
    subject_id: Identifier | None = None
    grade_level_id: Identifier | None = None
    resource_type_code: Annotated[str, Field(min_length=1, max_length=50)] | None = None
    resource_id: Identifier | None = None
    resource_version_id: Identifier | None = None


class SearchRequest(StrictDTO):
    query: str = Field(max_length=600)
    method: Method = 'hybrid'
    filters: SearchFilters = Field(default_factory=SearchFilters)
    page: int = Field(default=1, ge=1, le=10001)
    size: int = Field(default=10, ge=1, le=50)

    @field_validator('query')
    @classmethod
    def query_text(cls, value):
        value = normalize_query(value)
        if not 1 <= len(value) <= 300:
            raise ValueError('Invalid search query length')
        return value

    @model_validator(mode='after')
    def offset_bound(self):
        if (self.page - 1) * self.size > 10000:
            raise ValueError('Search offset exceeded')
        return self


class SearchItem(StrictDTO):
    resource_id: str
    resource_title: str
    resource_version_id: str
    version_number: int
    provider_name: str | None
    resource_type_code: str
    bag_id: str
    bag_title: str | None
    unit_type: str
    representation_mode: Literal['structured', 'fallback']
    matched_keywords: list[str]
    matched_bag_count: int
    score: float
    score_components: dict[str, float]
    snippet: str
    page_from: int | None
    page_to: int | None
    content_unit_id: str
    context_id: str
    context_url: str


class SearchResponse(StrictDTO):
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
    message: Literal['OK', 'NO_READY_INDEX', 'QUERY_NO_KEYWORDS']


MESSAGES = {
    'SEARCH_LIMIT_EXCEEDED': 'Kho dữ liệu vượt giới hạn tìm kiếm. Hãy thu hẹp bộ lọc.',
    'SEARCH_TIMEOUT': 'Tìm kiếm quá thời gian. Hãy thu hẹp bộ lọc.',
    'SEARCH_INDEX_STALE': 'Chỉ mục vừa thay đổi. Vui lòng tìm lại.',
    'BLOCKED_EMBEDDING_RUNTIME': 'Model embedding chưa sẵn sàng. Có thể chọn BoW.',
    'INVALID_EMBEDDING': 'Vector hoặc số chiều model không hợp lệ.',
    'BLOCKED_INDEX_SCHEMA': 'Schema MongoDB chưa hợp lệ.',
    'SEARCH_SCHEMA_UNAVAILABLE': 'Chưa kết nối được dữ liệu chỉ mục.',
    'CONTEXT_NOT_FOUND': 'Không tìm thấy ngữ cảnh.',
    'CONTEXT_STALE': 'Nội dung đã thay đổi. Vui lòng tìm lại.',
}


def search_error(exc):
    known = isinstance(exc, (SearchError, IndexingError))
    code = exc.code if known else 'SEARCH_UNAVAILABLE'
    return HTTPException(exc.status if known else 503, detail={
        'code': code, 'phase': 'search',
        'message': MESSAGES.get(code, 'Không thể truy cập dữ liệu tìm kiếm. Vui lòng thử lại.')})
