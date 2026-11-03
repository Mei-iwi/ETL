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


def test_contract_keeps_full_master_principal_separate_from_source_id_lengths():
    principal = 'master:' + ('F' + 'a' * 29)
    assert len(principal) == 37
    value = {**contract(), 'allowed_actor_ids': [principal]}
    policy = SourceOCRContract.from_owner_contract(value, expected_namespace='source-a')
    assert principal in policy.actor_ids
    assert policy.allows(namespace='source-a', table='ocr_jobs', action='insert', fields={'status'},
                         role='ADMIN', actor_id=principal, source_version_id='version-1', lifecycle='APPROVED')


@pytest.mark.parametrize('change', [
    {'entity_type': 'resources'}, {'actions': ['delete']}, {'tables': ['component_ocr_pages']},
    {'allowed_actor_ids': ['*'] * 2}, {'allowed_version_ids': []},
    {'allowed_lifecycles': ['REJECTED']}, {'statement_timeout_seconds': True},
    {'allow_etl_created_versions': 'true'}, {'allow_etl_created_versions': 1},
])
def test_invalid_or_metadata_contract_does_not_authorize_ocr(change):
    with pytest.raises(DomainError):
        SourceOCRContract.from_owner_contract({**contract(), **change}, expected_namespace='source-a')


def test_created_upload_policy_does_not_itself_authorize_arbitrary_versions():
    from unittest.mock import Mock

    from data_sync_etl.adapters.source.postgresql_ocr_write import PostgreSQLSourceOCRWriter

    policy = SourceOCRContract.from_owner_contract({**contract(), 'allowed_version_ids': [],
        'allow_etl_created_versions': True}, expected_namespace='source-a')
    request = dict(namespace='source-a', table='ocr_jobs', action='insert', fields={'status'}, role='ADMIN',
                   actor_id='actor-1', source_version_id='new-version', lifecycle='APPROVED')
    assert not policy.allows(**request)
    engine = Mock()
    engine.dialect.name = 'postgresql'
    writer = PostgreSQLSourceOCRWriter(engine, policy, authorize_identity=lambda _: True,
                                       authorize_created_version=lambda _: False)
    identity = {'source_version_id': 'new-version'}
    assert not writer.contract_for(identity).allows(**request)
    writer.authorize_created_version = lambda _: True
    assert writer.contract_for(identity).allows(**request)
    assert not policy.version_ids  # The shared policy is never enlarged or cached.
