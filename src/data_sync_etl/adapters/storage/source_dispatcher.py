"""Exact namespace routing; a provider failure never falls back to another provider."""

from data_sync_etl.domain.core import SourceFileError


class SourceFileDispatcher:
    def __init__(self, local, drive, *, local_buckets=()):
        self.local, self.drive = local, drive
        self.local_buckets = frozenset(local_buckets)
        if self.local_buckets.intersection(drive.roots):
            raise SourceFileError('AMBIGUOUS_SOURCE_BUCKET')

    def provider(self, bucket):
        if bucket in self.drive.roots:
            return 'google_drive'
        if self.local_buckets and bucket not in self.local_buckets:
            raise SourceFileError('SOURCE_BUCKET_NOT_ALLOWED')
        return 'local'

    def open_stream(self, bucket, object_key, *, cancelled=None):
        reader = self.drive if self.provider(bucket) == 'google_drive' else self.local
        return reader.open_stream(bucket, object_key, **({'cancelled': cancelled} if cancelled is not None else {}))

    def revision(self, bucket, object_key, *, cancelled=None):
        reader = self.drive if self.provider(bucket) == 'google_drive' else self.local
        return reader.revision(bucket, object_key, **({'cancelled': cancelled} if cancelled is not None else {}))

    def original_url(self, bucket, object_key):
        reader = self.drive if self.provider(bucket) == 'google_drive' else self.local
        return reader.original_url(bucket, object_key)

    def readiness(self, *, probe=False):
        result = {}
        for provider, reader in (('local', self.local), ('google_drive', self.drive)):
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
