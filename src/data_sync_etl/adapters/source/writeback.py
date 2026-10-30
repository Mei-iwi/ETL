"""Isolated fake writer; production composition always injects the disabled writer."""

from hashlib import sha256
from threading import Lock

from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.source_ownership import validate_source_writeback_fields


class DisabledSourceMetadataWriter:
    supported = False

    def supports(self, locator):
        return False

    def write_metadata(self, **kwargs):
        raise DomainError('SOURCE_WRITEBACK_DISABLED')


class InMemorySourceMetadataWriter:
    """Thread-safe demo CAS and payload-bound replay within one process."""

    supported = True

    def __init__(self, records=None, *, backend='fixture', namespace='fixture:source'):
        self.backend, self.namespace = backend, namespace
        self.records = {str(key): dict(value) for key, value in (records or {}).items()}
        self._replays = {}
        self._lock = Lock()

    def supports(self, locator):
        return (locator.backend == self.backend and locator.namespace == self.namespace
                and locator.entity_type == 'resources')

    def write_metadata(self, *, locator, fields, expected_source_state_hash, idempotency_key):
        fields = validate_source_writeback_fields(fields)
        if not self.supports(locator):
            raise DomainError('SOURCE_WRITEBACK_BLOCKED')
        payload = (locator.canonical_payload(), tuple(sorted(fields.items())), expected_source_state_hash)
        with self._lock:
            if idempotency_key in self._replays:
                before, result = self._replays[idempotency_key]
                if before != payload:
                    raise DomainError('SOURCE_WRITEBACK_IDEMPOTENCY_REUSED')
                return dict(result)
            current = self.records.get(locator.source_record_id)
            if current is None:
                raise DomainError('SOURCE_RECORD_NOT_FOUND')
            if (current.get('is_active', True) is not True or current.get('deleted_at')
                    or current.get('publication_status') == 'ARCHIVED'):
                raise DomainError('SOURCE_WRITEBACK_BLOCKED')
            if current.get('source_state_hash') != expected_source_state_hash:
                raise DomainError('SOURCE_WRITEBACK_CONFLICT')
            result = {'status': 'APPLIED', 'source_updated_at': now().isoformat(),
                      'source_state_hash': sha256(repr((payload, fields['title'])).encode()).hexdigest()}
            current.update(fields, source_state_hash=result['source_state_hash'])
            self._replays[idempotency_key] = (payload, dict(result))
            return result
