"""Private attempt-scoped staging, with cross-process slots and orphan cleanup.

Each directory is protected by an OS file lock held for its entire lifetime.
Cleanup never removes a locked directory, even when its TTL has elapsed.
"""

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from data_sync_etl.domain.core import SourceFileError


def _lock(file):
    try:
        file.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _plain(path):
    return not path.is_symlink() and not (hasattr(path, 'is_junction') and path.is_junction())


class SourceTemporaryFiles:
    def __init__(self, settings, managed_root):
        self.settings, self.managed_root = settings, Path(managed_root).resolve()

    def root(self):
        path = self.settings.source_temp_root or Path(tempfile.gettempdir()) / 'etl-source-reference'
        path = Path(path).absolute()
        # Refuse links at every level; cleanup must stay in an operator-owned root.
        if any(not _plain(p) for p in (path, *path.parents)):
            raise SourceFileError('INVALID_SOURCE_TEMP_ROOT')
        resolved = path.resolve()
        if resolved.is_relative_to(self.managed_root) or self.managed_root.is_relative_to(resolved):
            raise SourceFileError('INVALID_SOURCE_TEMP_ROOT')
        source_root = self.settings.source_file_storage_root
        if source_root:
            source_root = Path(source_root).resolve()
            if resolved.is_relative_to(source_root) or source_root.is_relative_to(resolved):
                raise SourceFileError('INVALID_SOURCE_TEMP_ROOT')
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != 'nt' and path.stat().st_mode & 0o077:
            raise SourceFileError('INVALID_SOURCE_TEMP_PERMISSIONS')
        return path

    def cleanup_orphans(self):
        root = self.root()
        removed = 0
        for directory in root.glob('attempt-*'):
            try:
                if not _plain(directory) or not directory.is_dir():
                    continue
                if time.time() - directory.stat().st_mtime < self.settings.source_temp_orphan_ttl_seconds:
                    continue
            except FileNotFoundError:
                continue  # another request completed while this sweep enumerated
            lease = directory / 'lease'
            if not _plain(lease) or not lease.is_file():
                continue
            try:
                with lease.open('r+b') as handle:
                    if not _lock(handle):
                        continue
                    # Only our leaf files, OCR scratch and bounded converter profiles.
                    children = list(directory.iterdir())
                    files, folders, safe = [], [], True
                    for child in children:
                        if _plain(child) and child.is_file():
                            files.append(child)
                        elif _plain(child) and child.is_dir() and child.name.startswith(('etl-ocr-', 'etl-preview-')):
                            pending = [child]
                            while pending and safe:
                                folder = pending.pop()
                                folders.append(folder)
                                for nested in folder.iterdir():
                                    if not _plain(nested) or not nested.resolve().is_relative_to(directory.resolve()):
                                        safe = False
                                        break
                                    if nested.is_file():
                                        files.append(nested)
                                    elif nested.is_dir() and child.name.startswith('etl-preview-'):
                                        pending.append(nested)
                                    else:
                                        safe = False
                                        break
                                    if len(files) + len(folders) + len(pending) > 20000:
                                        safe = False
                                        break
                        else:
                            safe = False
                            break
                    if not safe:
                        continue
                    for child in files:
                        if child != lease:
                            child.unlink()
                    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
                        folder.rmdir()
                # On Windows an open lock file cannot be unlinked.
                lease.unlink()
                directory.rmdir()
                removed += 1
            except (FileNotFoundError, PermissionError):
                continue
        return removed

    @contextmanager
    def scope(self):
        if not self.settings.source_temporary_files_enabled:
            raise SourceFileError('SOURCE_TEMP_POLICY_REQUIRED')
        try:
            root = self.root()
            self.cleanup_orphans()
        except SourceFileError:
            raise
        except OSError:
            raise SourceFileError('SOURCE_TEMP_UNAVAILABLE', transient=True) from None
        slot = None
        for i in range(self.settings.source_temp_max_concurrency):
            path = root / f'slot-{i}.lock'
            if not _plain(path):
                raise SourceFileError('INVALID_SOURCE_TEMP_ROOT')
            handle = None
            try:
                handle = path.open('a+b')
                handle.write(b'0')
                handle.flush()
            except OSError:
                if handle is not None:
                    handle.close()
                raise SourceFileError('SOURCE_TEMP_UNAVAILABLE', transient=True) from None
            if _lock(handle):
                slot = handle
                break
            handle.close()
        if slot is None:
            raise SourceFileError('SOURCE_BUSY', transient=True)
        directory = None
        try:
            directory = Path(tempfile.mkdtemp(prefix='attempt-', dir=root))
            with (directory / 'lease').open('w+b') as lease:
                lease.write(b'0')
                lease.flush()
                if not _lock(lease):
                    raise SourceFileError('SOURCE_BUSY', transient=True)
                yield directory
        except OSError:
            raise SourceFileError('SOURCE_TEMP_UNAVAILABLE', transient=True) from None
        finally:
            try:
                if directory is not None:
                    if not _plain(directory) or not directory.resolve().is_relative_to(root.resolve()):
                        raise SourceFileError('INVALID_SOURCE_TEMP_ROOT')
                    try:
                        shutil.rmtree(directory)
                    except OSError:
                        raise SourceFileError('SOURCE_TEMP_UNAVAILABLE', transient=True) from None
            finally:
                slot.close()
