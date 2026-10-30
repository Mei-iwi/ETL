import pytest

from data_sync_etl.adapters.source.writeback import InMemorySourceMetadataWriter
from data_sync_etl.application.source_metadata_writeback import SourceWriteBackError
from data_sync_etl.domain.source_ownership import state_hash


def prepare_writeback(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {
            'source_origin': 'POSTGRESQL', 'source_backend': 'fixture',
            'source_namespace': 'fixture:source', 'source_entity_type': 'resources',
            'source_record_key': 'resource-1', 'source_identity_version': 1,
        })
        resource = repo.get('master_learning_resources', 'resource-1')
        repo.update('master_learning_resources', 'resource-1', {'source_state_hash': state_hash(resource)})
        resource = repo.get('master_learning_resources', 'resource-1')
    writer = InMemorySourceMetadataWriter({
        'resource-1': {'title': resource['title'], 'source_state_hash': resource['source_state_hash']},
    })
    container.settings.source_metadata_writeback_enabled = True
    container.source_writeback.writer = writer
    return resource, writer


def test_writeback_is_disabled_by_default(container):
    with pytest.raises(SourceWriteBackError) as error:
        container.source_writeback.request(
            'resource-1', {'title': 'Blocked'}, expected_source_state_hash='a' * 64,
            idempotency_key='disabled-01', actor_id='local-admin', role='ADMIN', approve=True,
        )
    assert error.value.code == 'SOURCE_WRITEBACK_DISABLED'


def test_writeback_requires_approval_is_idempotent_and_uses_cas(container):
    resource, writer = prepare_writeback(container)
    expected = resource['source_state_hash']
    pending = container.source_writeback.request(
        'resource-1', {'title': 'Updated title'}, expected_source_state_hash=expected,
        idempotency_key='writeback-01', actor_id='local-admin', role='ADMIN', approve=False,
    )
    assert pending['status'] == 'WRITEBACK_PENDING_APPROVAL'
    assert writer.records['resource-1']['title'] == resource['title']

    applied = container.source_writeback.request(
        'resource-1', {'title': 'Updated title'}, expected_source_state_hash=expected,
        idempotency_key='writeback-01', actor_id='local-admin', role='ADMIN', approve=True,
    )
    assert applied['status'] == 'WRITEBACK_APPLIED'
    assert writer.records['resource-1']['title'] == 'Updated title'
    replay = container.source_writeback.request(
        'resource-1', {'title': 'Updated title'}, expected_source_state_hash=expected,
        idempotency_key='writeback-01', actor_id='local-admin', role='ADMIN', approve=True,
    )
    assert replay == applied

    with pytest.raises(SourceWriteBackError) as error:
        container.source_writeback.request(
            'resource-1', {'title': 'Another'}, expected_source_state_hash='b' * 64,
            idempotency_key='writeback-02', actor_id='local-admin', role='ADMIN', approve=True,
        )
    assert error.value.code == 'SOURCE_WRITEBACK_CONFLICT'


def test_writeback_field_whitelist_and_owner_policy(container):
    resource, _ = prepare_writeback(container)
    with pytest.raises(SourceWriteBackError) as error:
        container.source_writeback.request(
            'resource-1', {'publication_status': 'DRAFT'},
            expected_source_state_hash=resource['source_state_hash'],
            idempotency_key='writeback-03', actor_id='local-admin', role='ADMIN', approve=True,
        )
    assert error.value.code == 'SOURCE_WRITEBACK_FIELD_BLOCKED'

    with pytest.raises(SourceWriteBackError) as error:
        container.source_writeback.request(
            'resource-1', {'title': 'Denied'},
            expected_source_state_hash=resource['source_state_hash'],
            idempotency_key='writeback-04', actor_id='master:other', role='OPERATOR', owner_id='other',
            approve=True,
        )
    assert error.value.code == 'SOURCE_WRITEBACK_OWNER_REQUIRED'
