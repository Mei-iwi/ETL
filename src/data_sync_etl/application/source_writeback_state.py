"""Translate verified Master identities before comparing canonical SOURCE state."""

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import mapped_source_locator
from data_sync_etl.domain.source_ownership import state_hash


def canonical_source_hash(repo, resource, locator):
    if locator.backend.casefold() != 'postgresql':
        return resource['source_state_hash']
    values = {**resource, 'id': locator.source_record_id}
    creator_id = resource.get('created_by')
    if creator_id:
        creator = repo.get('master_users', creator_id)
        if creator is None or creator.get('source_origin') != 'POSTGRESQL':
            raise DomainError('SOURCE_WRITEBACK_CREATOR_MAPPING_REQUIRED')
        identity = mapped_source_locator(creator)
        if (identity.backend.casefold() != locator.backend.casefold()
                or identity.namespace != locator.namespace or identity.entity_type != 'users'):
            raise DomainError('SOURCE_WRITEBACK_CREATOR_MAPPING_REQUIRED')
        values['created_by'] = identity.source_record_id
    return state_hash(values)
