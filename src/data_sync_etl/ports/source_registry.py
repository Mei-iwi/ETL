from typing import Protocol

from data_sync_etl.domain.source_ids import SourceLocator
from data_sync_etl.domain.source_registry import SourceRegistration


class SourceRegistryPort(Protocol):
    def registrations(self) -> tuple[SourceRegistration, ...]: ...
    def adapter_for(self, locator: SourceLocator, capability: str = 'read_metadata'): ...
    def adapter_for_source_id(self, source_id: str, capability: str = 'read_metadata'): ...


class SourceIdentityMappingPort(Protocol):
    def resource_mapping(self, master_resource_id: str) -> dict | None: ...
