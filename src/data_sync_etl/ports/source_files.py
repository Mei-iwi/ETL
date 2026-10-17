from datetime import datetime
from typing import BinaryIO, NotRequired, Protocol, TypedDict


class ReadOnlySourceFilePort(Protocol):
    def check(self) -> None: ...
    def open_stream(self, bucket: str, object_key: str) -> BinaryIO: ...


class SourceVersionMetadata(TypedDict):
    source_version_id: str
    resource_id: str
    source_version_number: int
    parent_version_id: str | None
    source_hash: str
    source_lifecycle_status: str
    resource_active: NotRequired[bool]
    resource_deleted_at: NotRequired[datetime | None]
    updated_at: NotRequired[datetime]
    resource_title: NotRequired[str]
    storage_bucket: NotRequired[str]
    storage_object_key: NotRequired[str]


class SourceVersionPage(TypedDict):
    items: list[SourceVersionMetadata]
    next_cursor: str | None


class ReadOnlySourceVersionPort(Protocol):
    def get_version(self, source_version_id: str) -> SourceVersionMetadata | None: ...
    def version_page(self, after: str | None = None, size: int = 20) -> SourceVersionPage: ...
    def list_versions(self, after=None, size=20) -> dict: ...
