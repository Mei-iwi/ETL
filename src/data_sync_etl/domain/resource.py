from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO

from data_sync_etl.domain.core import DomainError


@dataclass(frozen=True)
class CreateResourceCommand:
    title: str
    resource_type_code: str
    provider_id: str
    provider_name: str
    filename: str
    file_stream: BinaryIO
    file_size: int
    declared_mime: str
    provider_type: str = "INTERNAL"
    resource_type_name_vi: str | None = None
    subject_id: str | None = None
    grade_level_id: str | None = None
    publication_status: str = "PUBLISHED"
    uploaded_by: str | None = None

    def validate(self) -> None:
        required = {
            "title": (self.title, 500),
            "resource_type_code": (self.resource_type_code, 50),
            "provider_id": (self.provider_id, 30),
            "provider_name": (self.provider_name, 255),
            "provider_type": (self.provider_type, 30),
        }
        for field, (value, max_length) in required.items():
            if not isinstance(value, str) or not value.strip():
                raise DomainError(f"{field} is required")
            if len(value.strip()) > max_length:
                raise DomainError(f"{field} exceeds maximum length")
        if self.resource_type_name_vi is not None and len(self.resource_type_name_vi) > 150:
            raise DomainError("resource_type_name_vi exceeds maximum length")
        if self.uploaded_by is not None and len(self.uploaded_by) > 30:
            raise DomainError("uploaded_by exceeds maximum length")
        if self.subject_id is not None and len(self.subject_id) > 30:
            raise DomainError("subject_id exceeds maximum length")
        if self.grade_level_id is not None and len(self.grade_level_id) > 30:
            raise DomainError("grade_level_id exceeds maximum length")
        if self.publication_status not in {"DRAFT", "PUBLISHED", "UNPUBLISHED"}:
            raise DomainError("Unsupported publication_status")
        if not self.filename or len(self.filename) > 255:
            raise DomainError("Invalid filename")
        if self.filename in {".", ".."} or "/" in self.filename or "\\" in self.filename:
            raise DomainError("Invalid filename")
        if self.file_size < 1:
            raise DomainError("Uploaded file is empty")


@dataclass(frozen=True)
class CreateResourceResult:
    id: str
    title: str
    resource_type_code: str
    media_category: str
    mime_type: str
    file_name: str
    file_size: int
    source_hash: str
    initial_version_id: str
    version_number: int
    lifecycle_status: str
    created_at: datetime
