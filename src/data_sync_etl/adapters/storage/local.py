import hashlib
import re
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import StoredFile

BLOCK_SIZE = 1024 * 1024


def stream_hash(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(BLOCK_SIZE):
        digest.update(chunk)
    return digest.hexdigest()


class LocalFilesystemStorageAdapter:
    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, bucket: str, object_key: str) -> Path:
        if (
            not bucket
            or not object_key
            or "\\" in bucket
            or "\\" in object_key
            or ":" in bucket
            or ":" in object_key
        ):
            raise DomainError("Invalid storage path")

        key = PurePosixPath(object_key)
        if (
            "/" in bucket
            or bucket in {".", ".."}
            or key.is_absolute()
            or any(part in {"", ".", ".."} for part in object_key.split("/"))
        ):
            raise DomainError("Invalid storage path")

        path = (self.root / bucket / Path(*key.parts)).resolve()
        if not path.is_relative_to(self.root):
            raise DomainError("Invalid storage path")
        return path

    def get_local_path(self, bucket: str, object_key: str) -> Path:
        path = self._resolve(bucket, object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def open_stream(self, bucket: str, object_key: str) -> BinaryIO:
        return self._resolve(bucket, object_key).open("rb")

    def exists(self, bucket: str, object_key: str) -> bool:
        return self._resolve(bucket, object_key).is_file()

    def snapshot(self, bucket: str, object_key: str) -> StoredFile:
        with self.open_stream(bucket, object_key) as stream:
            digest = stream_hash(stream)
        return StoredFile(
            bucket=bucket,
            object_key=object_key,
            source_hash=digest,
        )

    def image_path(self, task_id: str, claim_token: str) -> Path:
        safe_id = re.compile(r"[A-Za-z0-9_-]{1,100}")
        if not safe_id.fullmatch(task_id) or not safe_id.fullmatch(claim_token):
            raise DomainError("Invalid OCR image identifier")

        directory = self.root / ".ocr-tmp"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{task_id}-{claim_token}.png"