"""Resolve SOURCE identity directly from identity columns on existing MASTER rows."""
from sqlalchemy import select

from data_sync_etl.db.models import MasterLearningResource
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
from data_sync_etl.domain.source_ownership import state_hash


class SQLAlchemySourceIdentityMappingStore:
    def __init__(self, uow, source_registry):
        self.uow, self.source_registry = uow, source_registry

    def resource_mapping(self, master_resource_id):
        try:
            with self.uow() as repo:
                row = repo.session.get(MasterLearningResource, master_resource_id)
                if row is None:
                    return None
                if row.source_origin != 'POSTGRESQL' or row.source_identity_hash is None:
                    return None
                locator = SourceLocator(
                    version=row.source_identity_version, backend=row.source_backend,
                    namespace=row.source_namespace, entity_type=row.source_entity_type,
                    source_record_id=row.source_record_key)
                digest = source_identity_hash(locator)
                if row.source_identity_hash != digest:
                    raise DomainError('BLOCKED_SOURCE_MAPPING')
                if not row.source_state_hash or row.source_state_hash != state_hash(repo._dict(row)):
                    raise DomainError('BLOCKED_LOCAL_SOURCE_CHANGE')
                matching = [item for item in self.source_registry.registrations()
                            if item.backend.casefold() == locator.backend.casefold()
                            and item.namespace == locator.namespace]
                if len(matching) != 1:
                    raise DomainError('BLOCKED_SOURCE_MAPPING')
                registration = matching[0]
                return {
                    'master_resource_id': row.id, 'source_id': registration.source_id,
                    'identity_version': row.source_identity_version, 'backend': row.source_backend,
                    'namespace': row.source_namespace, 'entity_type': row.source_entity_type,
                    'source_record_key': row.source_record_key, 'identity_hash': row.source_identity_hash,
                    'source_revision': None, 'status': 'STALE' if row.source_missing_at else 'ACTIVE',
                }
        except DomainError:
            raise
        except Exception:
            raise DomainError('SOURCE_MAPPING_UNAVAILABLE') from None

    def link_resource(self, *, source_id, locator, master_resource_id, source_revision=None):
        if not isinstance(locator, SourceLocator) or locator.entity_type != 'resources':
            raise DomainError('SOURCE_MAPPING_INVALID')
        digest = source_identity_hash(locator)
        registration = next((item for item in self.source_registry.registrations()
                             if item.source_id == source_id), None)
        if (registration is None or not registration.enabled
                or not registration.capabilities.read_metadata
                or registration.backend.casefold() != locator.backend.casefold()
                or registration.namespace != locator.namespace):
            raise DomainError('SOURCE_MAPPING_REGISTRY_MISMATCH')
        with self.uow() as repo:
            resource = repo.session.get(MasterLearningResource, master_resource_id)
            if resource is None:
                raise DomainError('MASTER_MAPPING_TARGET_NOT_FOUND')
            stored = (resource.source_identity_version, resource.source_backend, resource.source_namespace,
                      resource.source_entity_type, resource.source_record_key, resource.source_identity_hash)
            expected = (locator.version, locator.backend.casefold(), locator.namespace,
                        locator.entity_type.casefold(), locator.source_record_id, digest)
            if stored != expected:
                raise DomainError('MASTER_MAPPING_IDENTITY_MISMATCH')
            if not resource.source_state_hash or resource.source_state_hash != state_hash(repo._dict(resource)):
                raise DomainError('BLOCKED_LOCAL_SOURCE_CHANGE')
            existing = repo.session.scalar(select(MasterLearningResource).where(
                MasterLearningResource.source_backend == locator.backend.casefold(),
                MasterLearningResource.source_namespace == locator.namespace,
                MasterLearningResource.source_entity_type == locator.entity_type.casefold(),
                MasterLearningResource.source_record_key == locator.source_record_id).limit(1))
            if existing is None or existing.id != master_resource_id:
                raise DomainError('SOURCE_MAPPING_CONFLICT')
            return master_resource_id
