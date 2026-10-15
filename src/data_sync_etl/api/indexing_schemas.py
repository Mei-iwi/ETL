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


def index_error(exc: Exception):
    from fastapi import HTTPException

    code = exc.code if isinstance(exc, IndexingError) else "INDEX_UNAVAILABLE"
    return HTTPException(exc.status if isinstance(exc, IndexingError) else 503,
                         detail=IndexErrorDetail(code=code, message=MESSAGES.get(code, MESSAGES["INDEX_FAILED"])).model_dump())


ERROR_RESPONSES = {code: {"model": IndexErrorResponse} for code in (404, 409, 422, 503)}
