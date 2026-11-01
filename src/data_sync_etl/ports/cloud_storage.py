"""Provider-neutral object storage contracts.

The application talks to this port rather than importing an S3/Neon/R2 SDK.
Implementations must keep object locators private and must fail closed when a
provider cannot be authenticated or validated.
"""

from dataclasses import dataclass, field
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class CloudObject:
    provider_id: str
    bucket: str
    object_key: str = field(repr=False)
    size: int
    sha256: str
    content_type: str | None = None
    etag: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CloudStorageHealth:
    provider: str
    provider_id: str
    endpoint_host: str
    configured: bool
    reachable: bool | None = None
    error_code: str | None = None


class CloudStoragePort(Protocol):
    provider_id: str
    bucket: str

    def validate_config(self) -> None: ...
    def health(self, *, probe: bool = False) -> CloudStorageHealth: ...
    def put_object(self, stream: BinaryIO, *, object_key: str, content_type: str,
                   max_size_bytes: int | None = None, expected_sha256: str | None = None) -> CloudObject: ...
    def head_object(self, object_key: str) -> CloudObject: ...
    def get_stream(self, object_key: str, *, byte_range: tuple[int, int] | None = None) -> BinaryIO: ...
    def presign_read(self, object_key: str, *, ttl_seconds: int) -> str: ...
    def presign_upload(self, object_key: str, *, content_type: str, ttl_seconds: int) -> str: ...
    def delete_object(self, object_key: str) -> None: ...
