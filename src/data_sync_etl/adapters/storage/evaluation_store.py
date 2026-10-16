"""Bounded, checksummed JSON files; no database or process-local-only locks."""
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from data_sync_etl.domain.evaluation import EvaluationError, canonical, checksum, validate_id


class EvaluationFileStore:
    def __init__(self, root, *, max_files=200, max_bytes=8 * 1024 * 1024):
        self.root = Path(root).resolve()
        self.max_files, self.max_bytes = max_files, max_bytes

    def directory(self, kind='', *, create=False):
        if kind not in ('', 'datasets', 'runs'):
            raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503)
        path = self.root
        for segment in ['evaluations', *([kind] if kind else [])]:
            path = path / segment
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503)
            if create:
                path.mkdir(exist_ok=True, parents=True)
        return path

    @contextmanager
    def lock(self, name):
        if name not in ('store', 'run'):
            raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503)
        path = self.directory(create=True) / f'.{name}.lock'
        try:
            handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise EvaluationError('EVALUATION_BUSY', 409) from None
        except OSError:
            raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503) from None
        try:
            os.close(handle)
            yield
        finally:
            path.unlink(missing_ok=True)

    def put(self, kind, payload, *, replace=False):
        identifier = validate_id(payload['id'])
        data = canonical({'schema_version': 1, 'kind': kind, 'payload': payload, 'checksum': checksum(payload)}).encode('utf-8')
        if len(data) > self.max_bytes:
            raise EvaluationError('EVALUATION_SIZE_LIMIT')
        temporary = None
        try:
            with self.lock('store'):
                directory = self.directory(kind, create=True)
                target = directory / f'{identifier}.json'
                if target.is_symlink():
                    raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503)
                if target.exists() and not replace:
                    raise EvaluationError('EVALUATION_EXISTS', 409)
                if replace and (kind != 'runs' or not target.exists()):
                    raise EvaluationError('EVALUATION_NOT_FOUND', 404)
                if not replace and sum(1 for _ in directory.glob('*.json')) >= self.max_files:
                    raise EvaluationError('EVALUATION_STORAGE_QUOTA')
                fd, name = tempfile.mkstemp(prefix='.pending-', dir=directory)
                temporary = Path(name)
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)  # Protected by cross-process store lock.
        except OSError:
            raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503) from None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def get(self, kind, identifier):
        validate_id(identifier)
        try:
            path = self.directory(kind) / f'{identifier}.json'
            if path.is_symlink() or not path.resolve().is_relative_to(self.root):
                raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503)
            with path.open('rb') as stream:
                data = stream.read(self.max_bytes + 1)
            if len(data) > self.max_bytes:
                raise ValueError('size')
            document = json.loads(data)
            payload = document['payload']
            if (document['schema_version'] != 1 or document['kind'] != kind
                    or payload['id'] != identifier or document['checksum'] != checksum(payload)):
                raise ValueError('checksum')
            return payload
        except FileNotFoundError:
            raise EvaluationError('EVALUATION_NOT_FOUND', 404) from None
        except (OSError, ValueError, KeyError, TypeError):
            raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503) from None
