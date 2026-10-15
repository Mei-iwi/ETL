from typing import Literal

from pydantic import BaseModel

from data_sync_etl.domain.indexing import IndexingError

MESSAGES = {
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


IndexStatus = Literal["CONTENT_NOT_READY", "NOT_STARTED", "PENDING", "RUNNING", "READY",
                      "FAILED", "STALE", "BLOCKED_RUNTIME"]


class BowTerm(BaseModel):
    term: str
    frequency: int


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
    bow_top_terms: list[BowTerm]
    bow_term_count: int
    bow_length: int
    vector_ready: bool
    embedding_model: str
    embedding_version: str
    embedding_dimension: int


class ChunksResponse(BaseModel):
    resource_version_id: str
    index_run_id: str | None
    index_status: IndexStatus
    total: int
    offset: int
    limit: int
    items: list[ChunkItem]


def index_error(exc: Exception):
    from fastapi import HTTPException

    code = exc.code if isinstance(exc, IndexingError) else "INDEX_UNAVAILABLE"
    return HTTPException(exc.status if isinstance(exc, IndexingError) else 503,
                         detail=IndexErrorDetail(code=code, message=MESSAGES.get(code, MESSAGES["INDEX_FAILED"])).model_dump())


ERROR_RESPONSES = {code: {"model": IndexErrorResponse} for code in (404, 409, 422, 503)}
