"""Read-only source root; no filesystem mutation and no browser-supplied paths."""

import os
import re
import stat
from pathlib import Path

from data_sync_etl.domain.core import SourceFileError


class SourceLocalFileReader:
    def original_url(self, bucket, object_key):
        return None

    def viewer_reference(self, bucket, object_key):
        return None

    def revision(self, bucket, object_key, *, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise SourceFileError('SOURCE_REQUEST_CANCELLED')
        with self.open_stream(bucket, object_key) as stream:
            value = os.fstat(stream.fileno())
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns

    def __init__(self, root, destination_root):
        self.root = Path(root) if root is not None else None
        self.destination_root = Path(destination_root).resolve()

    def check(self):
        if self.root is None or not self.root.is_absolute():
            raise SourceFileError("BLOCKED_SOURCE_STORAGE")
        if self.root.is_symlink() or (hasattr(self.root, 'is_junction') and self.root.is_junction()):
            raise SourceFileError('INVALID_SOURCE_PATH')
        try:
            root = self.root.resolve(strict=True)
            if (not root.is_dir() or not os.access(root, os.R_OK | os.X_OK)
                    or root.is_relative_to(self.destination_root)
                    or self.destination_root.is_relative_to(root)):
                raise SourceFileError("BLOCKED_SOURCE_STORAGE")
        except OSError:
            raise SourceFileError("BLOCKED_SOURCE_STORAGE") from None

    def open_stream(self, bucket, object_key, *, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise SourceFileError('SOURCE_REQUEST_CANCELLED')
        self.check()
        parts = [bucket, *object_key.split("/")]
        # Reject Windows device names, ADS, UNC, control characters and ambiguous segments.
        reserved = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)
        if "/" in bucket or any(
            not part or part in {".", ".."} or part[-1:] in {".", " "}
            or any(ord(c) < 32 or c in '\\:<>"|?*' for c in part)
            or reserved.match(part) for part in parts
        ):
            raise SourceFileError("INVALID_SOURCE_PATH")
        try:
            root = self.root.resolve(strict=True)
            path = root
            for part in parts:
                path = path / part
                if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                    raise SourceFileError("INVALID_SOURCE_PATH")
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise SourceFileError("INVALID_SOURCE_PATH")
            if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                # Traverse with directory handles on POSIX so parent replacement cannot
                # redirect the open through a symlink outside the configured root.
                directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    for part in parts[:-1]:
                        following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                        os.close(directory)
                        directory = following
                    descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                        os.close(descriptor)
                        raise SourceFileError("INVALID_SOURCE_PATH")
                    return os.fdopen(descriptor, "rb")
                finally:
                    os.close(directory)
            # NOFOLLOW protects the final component on platforms supporting it.
            descriptor = os.open(resolved, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_BINARY", 0))
            try:
                opened = os.fstat(descriptor)
                current = path.stat()
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                    raise SourceFileError("INVALID_SOURCE_PATH")
                # Recheck parents after opening to reduce symlink replacement races.
                parent = path
                while parent != root:
                    if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
                        raise SourceFileError("INVALID_SOURCE_PATH")
                    parent = parent.parent
                return os.fdopen(descriptor, "rb")
            except BaseException:
                os.close(descriptor)
                raise
        except FileNotFoundError:
            raise SourceFileError("FILE_NOT_FOUND") from None
        except PermissionError:
            raise SourceFileError("PERMISSION_DENIED") from None
        except OSError:
            raise SourceFileError("SOURCE_STORAGE_UNAVAILABLE", transient=True) from None
