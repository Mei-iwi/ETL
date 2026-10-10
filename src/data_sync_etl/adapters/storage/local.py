import hashlib
import os
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import StoredFile

BLOCK_SIZE = 1024 * 1024


def stream_hash(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while True:
        block = stream.read(BLOCK_SIZE)
        if not block:
            break
        digest.update(block)
    return digest.hexdigest()


class LocalFilesystemStorageAdapter:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, bucket: str, object_key: str) -> Path:
        if not bucket or not object_key or any(x in bucket for x in ("/", "\\", ":")):
            raise DomainError("Invalid storage location")
        key = PurePosixPath(object_key)
        if key.is_absolute() or any(part in {"", ".", ".."} for part in key.parts) or ":" in object_key or "\\" in object_key:
            raise DomainError("Invalid storage location")
        path = (self.root / bucket / Path(*key.parts)).resolve()
        if not path.is_relative_to(self.root):
            raise DomainError("Invalid storage location")
        return path

    def get_local_path(self, bucket: str, object_key: str) -> Path:
        return self._safe_path(bucket, object_key)

    def exists(self, bucket: str, object_key: str) -> bool:
        return self._safe_path(bucket, object_key).is_file()

    def open_stream(self, bucket: str, object_key: str) -> BinaryIO:
        return self._safe_path(bucket, object_key).open("rb")

    def snapshot(self, bucket: str, object_key: str) -> StoredFile:
        path = self._safe_path(bucket, object_key)
        if not path.is_file():
            raise DomainError("Stored file not found")
        with path.open("rb") as stream:
            digest = stream_hash(stream)
        return StoredFile(bucket, object_key, digest)

    def save_stream(
        self,
        stream: BinaryIO,
        *,
        max_size_bytes: int | None = None,
        suffix: str = ".bin",
    ) -> StoredFile:
        digest = hashlib.sha256()
        size = 0
        resource_root = self.root / "resources"
        resource_root.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=resource_root)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "wb") as target:
                while True:
                    block = stream.read(BLOCK_SIZE)
                    if not block:
                        break
                    size += len(block)
                    if max_size_bytes is not None and size > max_size_bytes:
                        raise DomainError("Uploaded file exceeds the maximum allowed size")
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if size < 1:
                raise DomainError("Uploaded file is empty")
            source_hash = digest.hexdigest()
            object_key = f"{source_hash[:2]}/{source_hash}{suffix.lower()}"
            destination = self._safe_path("resources", object_key)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                temporary.unlink(missing_ok=True)
                return StoredFile("resources", object_key, source_hash, created=False)
            os.replace(temporary, destination)
            return StoredFile("resources", object_key, source_hash, created=True)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def delete(self, bucket: str, object_key: str) -> None:
        self._safe_path(bucket, object_key).unlink(missing_ok=True)

    def image_path(self, task_id: str, claim_token: str) -> Path:
        for value in (task_id, claim_token):
            if not value or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in value):
                raise DomainError("Invalid image path identifier")
        path = (self.root / "ocr-images" / task_id / f"{claim_token}.png").resolve()
        if not path.is_relative_to(self.root):
            raise DomainError("Invalid image path")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path
