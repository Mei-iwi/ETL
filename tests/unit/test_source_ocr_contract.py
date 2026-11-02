import pytest

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ocr_contract import SourceOCRContract


def contract():
    return {'backend': 'postgresql', 'entity_type': 'ocr_processing', 'schema': 'public',
            'namespace': 'source-a', 'tables': ['ocr_jobs', 'ocr_page_tasks'],
            'columns': {'ocr_jobs': ['id', 'status'], 'ocr_page_tasks': ['id', 'status']},
            'actions': ['select', 'insert', 'update'], 'allowed_roles': ['ADMIN'],
            'allowed_actor_ids': ['actor-1'], 'allowed_version_ids': ['version-1'],
            'allowed_lifecycles': ['APPROVED'], 'statement_timeout_seconds': 30}


def test_owner_ocr_contract_requires_all_dimensions():
    policy = SourceOCRContract.from_owner_contract(contract(), expected_namespace='source-a')
    request = dict(namespace='source-a', table='ocr_jobs', action='insert', fields={'status'}, role='ADMIN',
                   actor_id='actor-1', source_version_id='version-1', lifecycle='APPROVED')
    assert policy.allows(**request)
    for field, value in [('namespace', 'source-b'), ('table', 'content_units'), ('action', 'delete'),
                         ('fields', {'raw_text'}), ('fields', set()),
                         ('role', 'USER'), ('actor_id', 'other'), ('source_version_id', 'other'),
                         ('lifecycle', 'REJECTED')]:
        assert not policy.allows(**{**request, field: value})


@pytest.mark.parametrize('change', [
    {'entity_type': 'resources'}, {'actions': ['delete']}, {'tables': ['component_ocr_pages']},
    {'allowed_actor_ids': ['*'] * 2}, {'allowed_version_ids': []},
    {'allowed_lifecycles': ['REJECTED']}, {'statement_timeout_seconds': True},
])
def test_invalid_or_metadata_contract_does_not_authorize_ocr(change):
    with pytest.raises(DomainError):
        SourceOCRContract.from_owner_contract({**contract(), **change}, expected_namespace='source-a')
