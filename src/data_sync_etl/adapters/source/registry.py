from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SourceLocator
from data_sync_etl.domain.source_registry import SourceRegistration


class ConfiguredSourceRegistry:
    """Explicit server-side adapter registry. It never picks a fallback source."""

    def __init__(self, entries):
        entries = tuple(entries)
        if not 1 <= len(entries) <= 32:
            raise DomainError('SOURCE_REGISTRY_SIZE_INVALID')
        registrations = {}
        adapters = {}
        namespaces = set()
        for registration, adapter in entries:
            registration = (registration if isinstance(registration, SourceRegistration)
                            else SourceRegistration.model_validate(registration))
            if registration.capabilities.external_viewer and not registration.viewer_hosts:
                raise DomainError('SOURCE_VIEWER_ALLOWLIST_REQUIRED')
            if registration.source_id in registrations or registration.namespace in namespaces:
                raise DomainError('SOURCE_REGISTRY_DUPLICATE_IDENTITY')
            if not registration.enabled:
                adapters[registration.source_id] = adapter
                registrations[registration.source_id] = registration
                namespaces.add(registration.namespace)
                continue
            if not callable(getattr(adapter, 'locator', None)):
                raise DomainError('SOURCE_REGISTRY_ADAPTER_INVALID')
            try:
                probe = adapter.locator('resources', '__registry_probe__')
            except Exception:
                raise DomainError('SOURCE_REGISTRY_ADAPTER_INVALID') from None
            if probe.backend.casefold() != registration.backend.casefold() or probe.namespace != registration.namespace:
                raise DomainError('SOURCE_REGISTRY_ADAPTER_IDENTITY_MISMATCH')
            registrations[registration.source_id] = registration
            adapters[registration.source_id] = adapter
            namespaces.add(registration.namespace)
        if not registrations:
            raise DomainError('SOURCE_REGISTRY_EMPTY')
        self._registrations = registrations
        self._adapters = adapters

    def registrations(self):
        return tuple(self._registrations.values())

    def adapter_for(self, locator: SourceLocator, capability='read_metadata'):
        match = next((item for item in self._registrations.values()
                      if item.backend.casefold() == locator.backend.casefold()
                      and item.namespace == locator.namespace), None)
        if match is None:
            raise DomainError('SOURCE_REGISTRY_NAMESPACE_UNKNOWN')
        if not match.enabled:
            raise DomainError('SOURCE_REGISTRY_SOURCE_DISABLED')
        if capability not in ('read_metadata', 'read_content', 'exact_resource_lookup',
                               'exact_version_lookup', 'file_reference_lookup', 'external_viewer',
                               'remote_search', 'write_metadata'):
            raise DomainError('SOURCE_CAPABILITY_INVALID')
        if not getattr(match.capabilities, capability):
            raise DomainError('SOURCE_CAPABILITY_BLOCKED')
        return self._adapters[match.source_id]

    def adapter_for_source_id(self, source_id, capability='read_metadata'):
        match = self._registrations.get(source_id)
        if match is None:
            raise DomainError('SOURCE_REGISTRY_NAMESPACE_UNKNOWN')
        if not match.enabled:
            raise DomainError('SOURCE_REGISTRY_SOURCE_DISABLED')
        if capability not in ('read_metadata', 'read_content', 'exact_resource_lookup',
                               'exact_version_lookup', 'file_reference_lookup', 'external_viewer',
                               'remote_search', 'write_metadata'):
            raise DomainError('SOURCE_CAPABILITY_INVALID')
        if not getattr(match.capabilities, capability):
            raise DomainError('SOURCE_CAPABILITY_BLOCKED')
        return self._adapters[source_id]

    def public_status(self):
        return tuple({
            'source_id': item.source_id,
            'backend': item.backend,
            'namespace': item.namespace,
            'adapter_type': item.adapter_type,
            'enabled': item.enabled,
            'capabilities': item.capabilities.model_dump(),
        } for item in self._registrations.values())

    def health(self, source_id):
        registration = self._registrations.get(source_id)
        if registration is None:
            return {'source_id': source_id, 'status': 'UNKNOWN', 'safe_error_code': 'SOURCE_UNKNOWN'}
        if not registration.enabled:
            return {'source_id': source_id, 'status': 'DISABLED', 'safe_error_code': None}
        adapter = self._adapters[source_id]
        probe = getattr(adapter, 'health', None)
        if not callable(probe):
            return {'source_id': source_id, 'status': 'UNSUPPORTED',
                    'safe_error_code': 'SOURCE_HEALTH_UNSUPPORTED'}
        try:
            probe()
            return {'source_id': source_id, 'status': 'HEALTHY', 'safe_error_code': None}
        except Exception:
            return {'source_id': source_id, 'status': 'UNAVAILABLE',
                    'safe_error_code': 'SOURCE_UNAVAILABLE'}
