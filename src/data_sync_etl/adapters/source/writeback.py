"""Opt-in SOURCE metadata writers.

The production PostgreSQL adapter intentionally implements only SourceDataReader.
The in-memory writer is an isolated demo/test double and never writes fixture files
or a live SOURCE database.
"""

from hashlib import sha256

from data_sync_etl.domain.core import DomainError, now


class DisabledSourceMetadataWriter:
    supported = False

    def write_metadata(self, **kwargs):
        raise DomainError('SOURCE_WRITEBACK_DISABLED')


class InMemorySourceMetadataWriter:
    """Bounded fake writer with compare-and-swap and replay protection."""

    supported = True

    def __init__(self, records=None):
        self.records = {
            str(key): {'title': value.get('title'), 'source_state_hash': value.get('source_state_hash')}
            for key, value in (records or {}).items()
        }
        self._replays = {}

    @staticmethod
    def _state_hash(record_id, title):
        return sha256(f'ETL-SOURCE-DEMO|{record_id}|{title}'.encode()).hexdigest()

    def write_metadata(self, *, record_id, fields, expected_source_state_hash, idempotency_key):
        if idempotency_key in self._replays:
            return dict(self._replays[idempotency_key])
        current = self.records.get(record_id)
        if current is None:
            raise DomainError('SOURCE_RECORD_NOT_FOUND')
        if current.get('source_state_hash') != expected_source_state_hash:
            raise DomainError('SOURCE_WRITEBACK_CONFLICT')
        title = fields['title']
        updated = {
            'status': 'APPLIED',
            'source_updated_at': now().isoformat(),
            'source_state_hash': self._state_hash(record_id, title),
        }
        current.update(title=title, source_state_hash=updated['source_state_hash'])
        self._replays[idempotency_key] = dict(updated)
        return updated
