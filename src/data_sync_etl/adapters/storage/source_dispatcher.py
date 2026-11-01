"""Exact namespace routing; a provider failure never falls back to another provider."""

from data_sync_etl.domain.core import SourceFileError


class SourceFileDispatcher:
    def __init__(self, local, drive, *, object_reader=None, local_buckets=(), object_buckets=()):
        self.local, self.drive, self.object = local, drive, object_reader
        self.local_buckets = frozenset(local_buckets)
        self.object_buckets = frozenset(object_buckets or (() if object_reader is None else (object_reader.bucket,)))
        drive_buckets = frozenset(drive.roots)
        if self.local_buckets.intersection(drive_buckets) or self.object_buckets.intersection(drive_buckets):
            raise SourceFileError('AMBIGUOUS_SOURCE_BUCKET')
        if self.local_buckets.intersection(self.object_buckets):
            raise SourceFileError('AMBIGUOUS_SOURCE_BUCKET')

    def provider(self, bucket):
        if bucket in self.object_buckets:
            if self.object is None:
                raise SourceFileError('SOURCE_BUCKET_NOT_ALLOWED')
            return 'object'
        if bucket in self.drive.roots:
            return 'google_drive'
        if self.local_buckets and bucket not in self.local_buckets:
            raise SourceFileError('SOURCE_BUCKET_NOT_ALLOWED')
        if self.local is None:
            raise SourceFileError('SOURCE_BUCKET_NOT_ALLOWED')
        return 'local'

    def open_stream(self, bucket, object_key, *, cancelled=None):
        provider = self.provider(bucket)
        reader = self.object if provider == 'object' else self.drive if provider == 'google_drive' else self.local
        return reader.open_stream(bucket, object_key, **({'cancelled': cancelled} if cancelled is not None else {}))

    def revision(self, bucket, object_key, *, cancelled=None):
        provider = self.provider(bucket)
        reader = self.object if provider == 'object' else self.drive if provider == 'google_drive' else self.local
        return reader.revision(bucket, object_key, **({'cancelled': cancelled} if cancelled is not None else {}))

    def original_url(self, bucket, object_key):
        provider = self.provider(bucket)
        reader = self.object if provider == 'object' else self.drive if provider == 'google_drive' else self.local
        return reader.original_url(bucket, object_key)

    def viewer_reference(self, bucket, object_key):
        provider = self.provider(bucket)
        reader = self.object if provider == 'object' else self.drive if provider == 'google_drive' else self.local
        lookup = getattr(reader, 'viewer_reference', None)
        return lookup(bucket, object_key) if callable(lookup) else None

    def viewer_hosts(self, bucket):
        if self.provider(bucket) == 'object':
            return self.object.viewer_hosts(bucket)
        return ()

    def readiness(self, *, probe=False):
        result = {}
        readers = [('local', self.local), ('google_drive', self.drive)]
        if self.object is not None:
            readers.append(('object', self.object))
        for provider, reader in readers:
            try:
                if provider == 'google_drive' and not probe:
                    reader.configuration_check()
                    result[provider] = {'status': 'DRIVE_NOT_CHECKED', 'error_code': None}
                else:
                    reader.check()
                    result[provider] = {'status': 'LOCAL_READY' if provider == 'local' else 'DRIVE_READY', 'error_code': None}
            except SourceFileError as exc:
                result[provider] = {'status': 'LOCAL_UNAVAILABLE' if provider == 'local' else 'DRIVE_UNAVAILABLE', 'error_code': exc.code}
        return result

    def check(self):
        states = self.readiness()
        if all(state['error_code'] for state in states.values()):
            raise SourceFileError('BLOCKED_SOURCE_STORAGE')
