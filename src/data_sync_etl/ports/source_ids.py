from typing import Protocol

from data_sync_etl.domain.source_ids import SourceLocator


class SourceIDCounter(Protocol):
    def next_sequence(self, prefix: str, period: str) -> int: ...


class SourceIdentityProvider(Protocol):
    source_backend: str
    source_namespace: str

    def locator(self, entity_type: str, source_record_id: str) -> SourceLocator: ...
