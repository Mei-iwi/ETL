from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, NotRequired, Protocol, TypedDict


class ReadOnlySourceFilePort(Protocol):
    def check(self) -> None: ...
    def open_stream(self, bucket: str, object_key: str, *, cancelled=None) -> BinaryIO: ...
    def revision(self, bucket: str, object_key: str, *, cancelled=None) -> tuple: ...
    def original_url(self, bucket: str, object_key: str) -> str | None: ...


class SourceVersionMetadata(TypedDict):
    source_version_id: str
    resource_id: str
    source_version_number: int
    parent_version_id: str | None
    source_hash: str
    source_lifecycle_status: str
    resource_active: NotRequired[bool]
    resource_publication_status: NotRequired[str]
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


@dataclass(frozen=True, repr=False)
class ResolvedFileReference:
    ownership: str
    target_version_id: str | None
    resource_id: str
    source_version_id: str | None
    provider: str
    bucket: str = field(repr=False)
    opaque_locator: str = field(repr=False)
    filename: str
    mime: str
    expected_sha256: str
    source_snapshot: dict | None = field(default=None, repr=False)


class SourceFileResolverPort(Protocol):
    def resolve(self, version_id: str) -> ResolvedFileReference: ...
    def temporary_path(self, reference: ResolvedFileReference, *, cancelled=None) -> AbstractContextManager[Path]: ...
    def display_reference(self, reference: ResolvedFileReference) -> dict: ...
