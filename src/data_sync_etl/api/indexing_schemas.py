from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.mongo_documents import BagUnitType

MESSAGES = {
    "BAG_STRUCTURE_UNAVAILABLE": "Chưa có root hoặc ContentUnit lá tương thích để xây ContentBag; vẫn có thể xem Chunk.",
    "BAG_STRUCTURE_INVALID": "Cấu trúc nội dung có parent thiếu, khác phiên bản hoặc vòng lặp.",
    "LEAF_KEYWORDS_UNAVAILABLE": "Chưa có từ khóa hợp lệ cho ContentUnit lá.",
    "INVALID_INDEX_QUERY": "Tham số xem Chunk không hợp lệ.",
    "VERSION_NOT_FOUND": "Không tìm thấy phiên bản học liệu.",
    "CONTENT_NOT_READY": "Chưa hoàn tất trích xuất và chuẩn hóa nội dung.",
    "BLOCKED_EMBEDDING_RUNTIME": "Không thể tạo vector vì chưa cấu hình model local hợp lệ.",
    "BLOCKED_INDEX_SCHEMA": "Chưa cài đặt schema chỉ mục MongoDB hợp lệ.",
    "INDEX_ALREADY_RUNNING": "Phiên bản đang có tác vụ xử lý, vui lòng kiểm tra lại trạng thái.",
    "REINDEX_REQUIRED": "Lần lập chỉ mục trước thất bại; hãy chọn Tái lập chỉ mục.",
    "EMPTY_CONTENT": "Phiên bản không có nội dung văn bản để lập chỉ mục.",
    "INDEX_LIMIT_EXCEEDED": "Nội dung vượt giới hạn lập chỉ mục đã cấu hình.",
    "INDEX_SOURCE_CHANGED": "Nội dung đã thay đổi; cần tái lập chỉ mục.",
    "INDEX_ARTIFACT_INVALID": "Dữ liệu chỉ mục chưa đầy đủ hoặc không hợp lệ.",
    "INVALID_EMBEDDING": "Vector không hợp lệ hoặc không đúng số chiều model.",
    "INDEX_FAILED": "Không thể hoàn thành lập chỉ mục; hãy kiểm tra cấu hình và thử lại.",
    "INDEX_UNAVAILABLE": "Không thể kết nối dữ liệu lập chỉ mục.",
}


class IndexErrorDetail(BaseModel):
    code: str
    phase: Literal["indexing"] = "indexing"
    message: str


class IndexErrorResponse(BaseModel):
    detail: IndexErrorDetail


class IndexRequestResponse(BaseModel):
    resource_version_id: str
    index_run_id: str
    index_status: Literal["PENDING", "RUNNING", "READY"]
    action: Literal["QUEUED", "IN_PROGRESS", "ALREADY_INDEXED"]
    status_url: str


class ReindexRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


IndexStatus = Literal["CONTENT_NOT_READY", "NOT_STARTED", "PENDING", "RUNNING", "READY",
                      "FAILED", "STALE", "BLOCKED_RUNTIME", "BLOCKED_STRUCTURE"]


class ChunkItem(BaseModel):
    id: str
    content_unit_id: str
    chunk_sequence: int
    unit_chunk_sequence: int
    page_from: int | None
    page_to: int | None
    char_start: int
    char_end: int
    text: str
    review_status: str
    retrieval_eligible: bool
    leaf_keywords: list[str]


class ChunksResponse(BaseModel):
    resource_version_id: str
    index_run_id: str | None
    index_status: Literal['PREVIEW', 'CONTENT_NOT_READY']
    representation_status: Literal['PREVIEW', 'CONTENT_NOT_READY']
    chunk_storage: Literal['transient']
    total: int
    offset: int
    limit: int
    items: list[ChunkItem]


class BagSummary(BaseModel):
    id: str
    unit_type: BagUnitType
    representation_mode: Literal['structured', 'fallback']
    title: str | None
    keywords: list[str]
    embedding_text: str
    embedding_model: str
    embedding_dimension: int
    vector_ready: bool


class IndexStatusResponse(BaseModel):
    resource_version_id: str
    processing_status: str
    index_status: IndexStatus
    auto_enabled: bool
    latest_run_id: str | None
    published_run_id: str | None
    chunk_count: int
    chunk_preview_count: int
    bag_count: int
    representation_mode: Literal['structured', 'fallback', 'mixed', 'unavailable']
    bags: list[BagSummary]
    representation_unit: Literal['content_bag']
    chunk_storage: Literal['transient']
    bow_ready: bool
    vector_ready: bool
    algorithm: str
    chunk_size_words: int
    chunk_overlap_words: int
    embedding_model: str | None
    embedding_version: str | None
    embedding_dimension: int | None
    started_at: datetime | None
    finished_at: datetime | None
    error_code: str | None
    message: str


def index_error(exc: Exception):
    from fastapi import HTTPException

    code = exc.code if isinstance(exc, IndexingError) else "INDEX_UNAVAILABLE"
    return HTTPException(exc.status if isinstance(exc, IndexingError) else 503,
                         detail=IndexErrorDetail(code=code, message=MESSAGES.get(code, MESSAGES["INDEX_FAILED"])).model_dump())


ERROR_RESPONSES = {code: {"model": IndexErrorResponse} for code in (404, 409, 422, 503)}
