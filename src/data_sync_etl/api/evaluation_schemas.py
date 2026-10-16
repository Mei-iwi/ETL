from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import Field, field_validator, model_validator

from data_sync_etl.api.search_schemas import Method, SearchFilters, StrictDTO
from data_sync_etl.domain.evaluation import EvaluationError
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError, normalize_query

ItemId = Annotated[str, Field(min_length=1, max_length=30, pattern=r'^[A-Za-z0-9_-]+$')]


class Relevance(StrictDTO):
    item_id: ItemId
    grade: int = Field(ge=0, le=3)


class EvaluationQuery(StrictDTO):
    query_id: str = Field(min_length=1, max_length=50, pattern=r'^[A-Za-z0-9_-]+$')
    query: str = Field(min_length=1, max_length=600)
    relevance: list[Relevance] = Field(min_length=1, max_length=100)

    @field_validator('query')
    @classmethod
    def query_text(cls, value):
        value = normalize_query(value)
        if not 1 <= len(value) <= 300:
            raise ValueError('Query must contain 1..300 characters')
        return value

    @model_validator(mode='after')
    def unique_labels(self):
        ids = [row.item_id for row in self.relevance]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate relevance IDs')
        return self


class DatasetRequest(StrictDTO):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default='', max_length=1000)
    relevance_unit: Literal['resource'] = 'resource'
    ground_truth_confirmed: bool = False
    filters: SearchFilters = Field(default_factory=SearchFilters)
    queries: list[EvaluationQuery] = Field(min_length=1, max_length=100)

    @field_validator('name')
    @classmethod
    def name_text(cls, value):
        if not value.strip():
            raise ValueError('Dataset name is required')
        return value.strip()

    @model_validator(mode='after')
    def unique_queries(self):
        ids = [row.query_id for row in self.queries]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate query IDs')
        return self


class RunRequest(StrictDTO):
    dataset_id: str = Field(min_length=26, max_length=26)
    methods: list[Method] = Field(default_factory=lambda: ['bow', 'vector', 'hybrid'])
    k_values: list[int] = Field(default_factory=lambda: [1, 3, 5], min_length=1, max_length=20)
    max_results: int = Field(default=10, ge=1, le=20)
    warmup_queries: int = Field(default=0, ge=0, le=3)

    @model_validator(mode='after')
    def comparable_parameters(self):
        if sorted(self.methods) != ['bow', 'hybrid', 'vector']:
            raise ValueError('All three baseline methods are required exactly once')
        if self.k_values != sorted(set(self.k_values)) or any(k < 1 or k > self.max_results for k in self.k_values):
            raise ValueError('k values must be sorted, unique and <= max_results')
        return self


class DatasetResponse(StrictDTO):
    dataset_id: str
    version: int
    name: str
    number_of_queries: int
    relevant_items: int
    relevance_unit: Literal['resource']
    relevance_threshold: Literal[1]
    dataset_hash: str
    created_at: str
    validation: dict


class RunResponse(StrictDTO):
    run_id: str
    dataset_id: str
    status: Literal['RUNNING', 'COMPLETED', 'FAILED', 'PARTIAL', 'NOT_COMPARABLE']
    warnings: list[str]
    finished_at: str | None


class RunDetailResponse(StrictDTO):
    id: str
    dataset_id: str
    dataset_name: str
    dataset_hash: str
    schema_version: Literal[1]
    scope: Literal['local_demo']
    status: Literal['RUNNING', 'COMPLETED', 'FAILED', 'PARTIAL', 'NOT_COMPARABLE']
    created_at: str
    finished_at: str | None
    duration_ms: float | None = None
    params: RunRequest
    snapshot: dict
    corpus_fingerprint: str
    queries: list[EvaluationQuery]
    relevance_unit: Literal['resource']
    relevance_threshold: Literal[1]
    warnings: list[str]
    observations: list[dict]
    summary: dict
    measurement: dict
    pagination: dict


MESSAGES = {
    'EVALUATION_FORBIDDEN': 'Thực nghiệm chỉ khả dụng trong Development loopback đã xác minh.',
    'EVALUATION_NOT_FOUND': 'Không tìm thấy dataset hoặc lần thực nghiệm.',
    'EVALUATION_BUSY': 'Có tác vụ thực nghiệm đang chạy. Vui lòng thử lại.',
    'EVALUATION_STORAGE_UNAVAILABLE': 'Không đọc/ghi được dữ liệu thực nghiệm hoặc checksum không hợp lệ.',
    'EVALUATION_STORAGE_QUOTA': 'Đã đạt giới hạn lưu trữ thực nghiệm.',
    'EVALUATION_SIZE_LIMIT': 'Dữ liệu thực nghiệm vượt giới hạn cho phép.',
    'EVALUATION_QRELS_DENIED': 'Nhãn chứa học liệu không đủ quyền hoặc chưa Index READY.',
    'EVALUATION_NO_READY_CORPUS': 'Chưa có học liệu đủ quyền và Index READY.',
    'EVALUATION_DATASET_STALE': 'Chỉ mục hoặc policy đã thay đổi. Cần kiểm tra nhãn và tạo dataset mới.',
    'EVALUATION_REPORT_INCOMPLETE': 'Chỉ xuất báo cáo cho lần thực nghiệm COMPLETED.',
    'EVALUATION_CORPUS_LIMIT': 'Kho thử nghiệm vượt giới hạn an toàn.',
}


def evaluation_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, SearchError) and exc.status == 403:
        exc = EvaluationError('EVALUATION_FORBIDDEN', 403)
    if isinstance(exc, (SearchError, IndexingError)):
        return HTTPException(503, detail={'code': 'BLOCKED_SEARCH_DEPENDENCY', 'phase': 'evaluation',
                                         'message': 'Search/chỉ mục/model chưa sẵn sàng cho thực nghiệm.'})
    code, status = (exc.code, exc.status) if isinstance(exc, EvaluationError) else ('EVALUATION_STORAGE_UNAVAILABLE', 503)
    return HTTPException(status, detail={'code': code, 'phase': 'evaluation',
                                       'message': MESSAGES.get(code, 'Không thực hiện được yêu cầu thực nghiệm.')})
