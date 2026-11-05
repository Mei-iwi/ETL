"""Current SOURCE publication/hash contract, independent of legacy OCR fixtures."""

import pytest

from data_sync_etl.adapters.persistence.graph_mapping import PostgresGraphMapping
from data_sync_etl.adapters.persistence.related_learning import RelatedLearningAuthority
from data_sync_etl.adapters.source.registry import ConfiguredSourceRegistry
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_source_content_memory import grant_source_content
from tests.unit.test_source_file_import import discover_and_import
from tests.unit.test_source_file_import import importing as importing


@pytest.mark.parametrize('revocation', ['source_inactive', 'source_deleted', 'source_hash',
                                      'publication', 'lifecycle', 'mapping_inactive', 'derived_grant', 'missing_publication'])
def test_related_source_authority_requires_current_fields_and_rechecks_every_request(importing, revocation):
    container, source_rows, _ = importing
    row = discover_and_import(container)
    grant_source_content(container)  # Isolated fixture capabilities, never live grants.
    source_rows[0].update(resource_publication_status='PUBLISHED', resource_active=True,
                          resource_deleted_at=None)
    authority = RelatedLearningAuthority(container.uow, container.search,
        PostgresGraphMapping(container.engine), container.settings)
    access = SearchAccess('authenticated_admin')
    seed = row['resource_id']
    assert seed in authority.read(seed, access)
    if revocation == 'source_inactive':
        source_rows[0]['resource_active'] = False
    elif revocation == 'source_deleted':
        source_rows[0]['resource_deleted_at'] = 'revoked'
    elif revocation == 'source_hash':
        source_rows[0]['source_hash'] = '0' * 64
    elif revocation == 'publication':
        source_rows[0]['resource_publication_status'] = 'DRAFT'
    elif revocation == 'lifecycle':
        source_rows[0]['source_lifecycle_status'] = 'REJECTED'
    elif revocation == 'missing_publication':
        source_rows[0].pop('resource_publication_status')
    elif revocation == 'mapping_inactive':
        with container.uow() as repo:
            repo.update('source_file_imports', row['id'], {'source_resource_active': False})
    else:
        registration = container.source_resolver.source_registry.registrations()[0]
        denied = registration.model_copy(update={'capabilities': registration.capabilities.model_copy(
            update={'index_derived_content': False})})
        container.source_resolver.source_registry = ConfiguredSourceRegistry([(denied, container.source)])
    assert authority.read(seed, access) == {}
