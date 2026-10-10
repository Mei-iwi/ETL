from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredFile:
    bucket: str
    object_key: str
    source_hash: str


class StoragePort(Protocol):
    def open_stream(self, bucket: str, object_key: str) -> BinaryIO: ...
    def exists(self, bucket: str, object_key: str) -> bool: ...
    def get_local_path(self, bucket: str, object_key: str) -> Path: ...
    def snapshot(self, bucket: str, object_key: str) -> StoredFile: ...
    def image_path(self, task_id: str, claim_token: str) -> Path: ...


@dataclass(frozen=True)
class OCRResult:
    text: str = field(repr=False)
    confidence: float | None = None
    engine_name: str = "unknown"
    engine_version: str | None = None
    metadata: dict = field(default_factory=dict, repr=False)


class PDFProcessorPort(Protocol):
    def page_count(self, path: Path) -> int: ...
    def render_page_to_image(self, path: Path, page_num: int, output: Path) -> Path: ...
    def extract_native_text(self, path: Path, page_num: int) -> str: ...
    def has_significant_image(self, path: Path, page_num: int) -> bool: ...


class OCREnginePort(Protocol):
    def recognize(self, image_path: Path) -> OCRResult: ...
