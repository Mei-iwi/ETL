"""Audit-only byte accounting around the unchanged SOURCE resolver."""
import os
from contextlib import contextmanager
from copy import copy

from data_sync_etl.domain.core import SourceFileError


class MeteredFiles:
    def __init__(self, files, metrics, maximum, provider='local'):
        self.files, self.metrics, self.maximum = files, metrics, maximum
        if provider == 'google_drive':
            # Drive's existing reader downloads to a private temporary file
            # before returning a stream. Meter that download as well as hashing.
            if not hasattr(files, 'drive'):
                raise SourceFileError('AUDIT_PROVIDER_BUDGET_UNSUPPORTED')
            self.files = copy(files)
            self.files.drive = drive = copy(files.drive)
            original_get, original_validate = drive._get, drive._validate_blob
            download_started = False

            def validate(metadata, filename):
                size = original_validate(metadata, filename)
                # Download + subsequent SHA-256 read both consume I/O budget.
                if not download_started and 2 * size > maximum - metrics['bytes_read']:
                    raise SourceFileError('RECONCILIATION_BYTE_BUDGET')
                return size

            def get(*args, **kwargs):
                nonlocal download_started
                if kwargs.get('media'):
                    download_started = True
                response = original_get(*args, **kwargs)
                return MeteredResponse(response, metrics, maximum) if kwargs.get('media') else response

            drive._get, drive._validate_blob = get, validate
        elif provider != 'local':
            raise SourceFileError('AUDIT_PROVIDER_BUDGET_UNSUPPORTED')

    def __getattr__(self, name):
        return getattr(self.files, name)

    @contextmanager
    def open_stream(self, *args, **kwargs):
        with self.files.open_stream(*args, **kwargs) as stream:
            yield MeteredStream(stream, self.metrics, self.maximum)


class MeteredStream:
    def __init__(self, stream, metrics, maximum):
        self.stream, self.metrics, self.maximum = stream, metrics, maximum

    def read(self, size=-1):
        remaining = self.maximum - self.metrics['bytes_read']
        if remaining <= 0:
            try:
                if self.stream.tell() == os.fstat(self.stream.fileno()).st_size:
                    return b''
            except (OSError, AttributeError):
                # In-memory SOURCE test/adapter streams do not expose a file
                # descriptor.  When they expose a zero-copy buffer, use its
                # bounded length to distinguish a clean EOF from an attempted
                # read beyond the reconciliation budget.
                try:
                    buffer = self.stream.getbuffer()
                    if self.stream.tell() >= len(buffer):
                        return b''
                except (AttributeError, OSError, TypeError, ValueError):
                    pass
            raise SourceFileError('RECONCILIATION_BYTE_BUDGET')
        block = self.stream.read(min(remaining, size) if size >= 0 else remaining)
        self.metrics['bytes_read'] += len(block)
        return block

    def __getattr__(self, name):
        return getattr(self.stream, name)


class MeteredResponse:
    def __init__(self, response, metrics, maximum):
        self.response, self.metrics, self.maximum = response, metrics, maximum

    def __getattr__(self, name):
        return getattr(self.response, name)

    def iter_content(self, size):
        remaining = self.maximum - self.metrics['bytes_read']
        blocks = iter(self.response.iter_content(min(size, max(1, remaining))))
        while True:
            if self.metrics['bytes_read'] >= self.maximum:
                raise SourceFileError('RECONCILIATION_BYTE_BUDGET')
            try:
                block = next(blocks)
            except StopIteration:
                return
            self.metrics['bytes_read'] += len(block)
            if self.metrics['bytes_read'] > self.maximum:
                raise SourceFileError('RECONCILIATION_BYTE_BUDGET')
            yield block
