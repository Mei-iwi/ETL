from dataclasses import dataclass

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SourceIdentity, SourceLocator, mapped_source_locator
from data_sync_etl.ports.source_registry import SourceIdentityMappingPort, SourceRegistryPort


@dataclass(frozen=True)
class SourceRoute:
    adapter: object
    locator: SourceLocator
    master_resource_id: str
    source_id: str
    legacy_id_match: bool


class SourceRouter:
    """Resolve an exact server-side source identity without cross-source fallback."""

    def __init__(self, registry: SourceRegistryPort, mappings: SourceIdentityMappingPort | None = None):
        self.registry, self.mappings = registry, mappings

    def route(self, locator: SourceLocator, *, capability='read_metadata'):
        adapter = self.registry.adapter_for(locator, capability)
        expected = adapter.locator(locator.entity_type, locator.source_record_id)
        if expected != locator:
            raise DomainError('BLOCKED_SOURCE_IDENTITY')
        return adapter

    def route_resource(self, resource: dict) -> SourceRoute:
        master_id = resource.get('id')
        if not isinstance(master_id, str) or not master_id:
            raise DomainError('BLOCKED_SOURCE_IDENTITY')
        stored = mapped_source_locator(resource)
        mapping = self.mappings.resource_mapping(master_id) if self.mappings is not None else None
        legacy_id_match = mapping is None
        source_id = None
        locator = stored
        if mapping is not None:
            try:
                locator = SourceLocator(
                    version=mapping['identity_version'], backend=mapping['backend'],
                    namespace=mapping['namespace'], entity_type=mapping['entity_type'],
                    source_record_id=mapping['source_record_key'])
                SourceIdentity(locator=locator, digest=mapping['identity_hash'])
            except (KeyError, TypeError, ValueError):
                raise DomainError('BLOCKED_SOURCE_MAPPING') from None
            registrations = self.registry.registrations()
            matching = [item for item in registrations
                        if item.backend.casefold() == locator.backend.casefold()
                        and item.namespace == locator.namespace]
            if (mapping.get('master_resource_id') != master_id or mapping.get('status') != 'ACTIVE'
                    or locator != stored or mapping.get('identity_hash') != resource.get('source_identity_hash')
                    or len(matching) != 1 or mapping.get('source_id') != matching[0].source_id):
                raise DomainError('BLOCKED_SOURCE_MAPPING')
            source_id = mapping['source_id']
        elif len(self.registry.registrations()) > 1 or stored.source_record_id != master_id:
            raise DomainError('BLOCKED_SOURCE_MAPPING')
        else:
            matching = [item for item in self.registry.registrations()
                        if item.backend.casefold() == locator.backend.casefold()
                        and item.namespace == locator.namespace and item.enabled]
            if len(matching) != 1:
                raise DomainError('BLOCKED_SOURCE_MAPPING')
            source_id = matching[0].source_id
        adapter = self.route(locator, capability='exact_resource_lookup')
        return SourceRoute(adapter, locator, master_id, source_id, legacy_id_match)
