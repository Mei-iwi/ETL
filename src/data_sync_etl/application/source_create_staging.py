"""Durable, short-lived staging for files submitted to the SOURCE create flow.

The staged bytes are only a recovery aid between the HTTP request and the
SOURCE object upload.  They are never referenced by a Master resource and are
removed after the SOURCE object/version commit (or by the bounded orphan
cleanup routine).
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from pathlib import Path

from data_sync_etl.domain.core import DomainError, UploadTooLargeError

_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,100}$")
_BLOCK = 1024 * 1024


class SourceCreateStager:
    def __init__(self, root: Path, *, max_size_bytes: int = 512 * 1024 * 1024):
        self.root = Path(root).expanduser().resolve()
        self.max_size_bytes = max_size_bytes
        if type(max_size_bytes) is not int or max_size_bytes < 1:
            raise ValueError("Invalid SOURCE create staging size")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir() or self.root.is_symlink():
            raise DomainError("SOURCE_CREATE_STAGING_INVALID")

    def cleanup_orphans(self, *, ttl_seconds: int = 3600):
        cutoff = time.time() - max(60, ttl_seconds)
        for path in self.root.glob("*.upload"):
            try:
                if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
            except OSError:
                continue

    def stage(self, stream, *, idempotency_key: str, filename: str, content_type: str,
              file_size: int, source_hash: str | None = None) -> dict:
        self.cleanup_orphans()
        if not isinstance(idempotency_key, str) or not _KEY.fullmatch(idempotency_key):
            raise DomainError("SOURCE_WRITEBACK_IDEMPOTENCY_INVALID")
        if not isinstance(filename, str) or not filename.strip() or len(filename) > 255:
            raise DomainError("SOURCE_VERSION_FILENAME_INVALID")
        if not isinstance(content_type, str) or "/" not in content_type:
            raise DomainError("SOURCE_VERSION_MIME_INVALID")
        if type(file_size) is not int or file_size < 1:
            raise DomainError("SOURCE_VERSION_FILE_INVALID")
        if file_size > self.max_size_bytes:
            raise UploadTooLargeError("Uploaded file exceeds the SOURCE staging limit")

        key = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
        destination = self.root / f"{key}.upload"
        temporary = None
        digest = hashlib.sha256()
        size = 0
        try:
            fd, name = tempfile.mkstemp(prefix=f".{key}.", dir=self.root)
            temporary = Path(name)
            with os.fdopen(fd, "wb") as target:
                while True:
                    block = stream.read(_BLOCK)
                    if not block:
                        break
                    size += len(block)
                    if size > self.max_size_bytes:
                        raise UploadTooLargeError("Uploaded file exceeds the SOURCE staging limit")
                    digest.update(block)
                    target.write(block)
                target.flush()
                os.fsync(target.fileno())
            if size != file_size:
                raise DomainError("SOURCE_VERSION_FILE_SIZE_MISMATCH")
            calculated = digest.hexdigest()
            if source_hash is not None and calculated.lower() != source_hash.lower():
                raise DomainError("SOURCE_VERSION_HASH_INVALID")
            if destination.exists():
                with destination.open("rb") as current:
                    existing_hash = hashlib.sha256(current.read()).hexdigest()
                if existing_hash != calculated:
                    raise DomainError("SOURCE_CREATE_IDEMPOTENCY_REUSED")
                temporary.unlink(missing_ok=True)
            else:
                temporary.replace(destination)
            return {
                "path": str(destination),
                "filename": filename.strip(),
                "content_type": content_type.lower(),
                "file_size": size,
                "source_hash": calculated,
            }
        except OSError as exc:
            raise DomainError("SOURCE_CREATE_STAGING_UNAVAILABLE") from exc
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def open(self, manifest: dict):
        path = Path(str(manifest.get("path", ""))).resolve()
        if not path.is_relative_to(self.root) or not path.is_file() or path.is_symlink():
            raise DomainError("SOURCE_CREATE_STAGING_MISSING")
        return path.open("rb")

    def remove(self, manifest: dict | None):
        if not manifest:
            return
        path = Path(str(manifest.get("path", ""))).resolve()
        if path.is_relative_to(self.root) and path.is_file() and not path.is_symlink():
            path.unlink(missing_ok=True)
