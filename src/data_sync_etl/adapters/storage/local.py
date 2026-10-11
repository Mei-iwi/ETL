import hashlib
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable

from data_sync_etl.domain.core import DomainError, UploadTooLargeError
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
        source = self._resolve(bucket, object_key)
        if not source.is_file():
            raise DomainError("Stored file not found")
        with source.open("rb") as stream:
            return self.save_stream(stream, suffix=source.suffix or ".bin", allow_empty=True)

    def save_stream(
        self,
        stream: BinaryIO,
        *,
        max_size_bytes: int | None = None,
        suffix: str = ".bin",
        validate_path: Callable[[Path], None] | None = None,
        allow_empty: bool = False,
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
                        raise UploadTooLargeError("Uploaded file exceeds the maximum allowed size")
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if size < 1 and not allow_empty:
                raise DomainError("Uploaded file is empty")
            if validate_path is not None:
                validate_path(temporary)
            source_hash = digest.hexdigest()
            object_key = f"{source_hash[:2]}/{source_hash}{suffix.lower()}"
            (resource_root / source_hash[:2]).mkdir(parents=True, exist_ok=True)
            destination = self._resolve("resources", object_key)
            try:
                # A hard link publishes fully flushed bytes only if the hash key
                # does not exist. Unlike os.replace, this never overwrites a peer.
                os.link(temporary, destination)
            except FileExistsError:
                if not destination.is_file() or destination.is_symlink():
                    raise DomainError("Stored object is not a regular file") from None
                with destination.open("rb") as existing:
                    if stream_hash(existing) != source_hash:
                        raise DomainError("Stored object hash mismatch") from None
                return StoredFile("resources", object_key, source_hash, created=False)
            return StoredFile("resources", object_key, source_hash, created=True)
        finally:
            temporary.unlink(missing_ok=True)

    def delete(self, bucket: str, object_key: str) -> None:
        self._resolve(bucket, object_key).unlink(missing_ok=True)

    def image_path(self, task_id: str, claim_token: str) -> Path:
        safe_id = re.compile(r"[A-Za-z0-9_-]{1,100}")
        if not safe_id.fullmatch(task_id) or not safe_id.fullmatch(claim_token):
            raise DomainError("Invalid OCR image identifier")

        directory = self.root / ".ocr-tmp"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{task_id}-{claim_token}.png"
