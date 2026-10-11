from dataclasses import dataclass
from typing import BinaryIO

from data_sync_etl.domain.core import DomainError


@dataclass(frozen=True)
class CreateResourceVersionCommand:
    resource_id: str
    filename: str
    file_stream: BinaryIO
    file_size: int
    declared_mime: str
    uploaded_by: str | None = None

    def validate(self) -> None:
        if not self.resource_id or not self.resource_id.strip():
            raise DomainError("resource_id is required")
        if not self.filename or len(self.filename) > 255 or "/" in self.filename or "\\" in self.filename:
            raise DomainError("Invalid filename")
        if self.file_size < 1:
            raise DomainError("Uploaded file is empty")
        if self.uploaded_by is not None and len(self.uploaded_by.strip()) > 30:
            raise DomainError("uploaded_by exceeds maximum length")


@dataclass(frozen=True)
class CreateResourceVersionResult:
    action: str
    resource_id: str
    resource_version_id: str
    parent_version_id: str | None
    version_number: int
    lifecycle_status: str
    file_name: str
    file_size: int
    mime_type: str
    source_hash: str
    uploaded_by: str | None
    created_at: object
