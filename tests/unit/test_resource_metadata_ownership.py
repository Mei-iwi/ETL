from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.resource_metadata import (
    ResourceMetadataOwnershipError,
    assert_resource_metadata_editable,
)
from data_sync_etl.domain.source_ownership import state_hash
from data_sync_etl.main import create_app


def owned(container, origin='POSTGRESQL'):
    container.sync.run(full=True)
    with container.uow() as repo:
        for row in repo.find('master_learning_resources'):
            repo.update('master_learning_resources', row['id'], {
                'source_origin': origin, 'source_state_hash': state_hash(row) if origin == 'POSTGRESQL' else None})


def snapshot(container):
    with container.uow() as repo:
        return [repo.find(table, order=('resource_id',)) if table != 'master_learning_resources'
                else repo.find(table, order=('id',))
                for table in ('master_learning_resources', 'resource_subjects', 'resource_grade_levels')]


@pytest.mark.parametrize('payload', [
    {'title': 'New'}, {'publication_status': 'DRAFT'}, {'provider_id': 'provider-2'},
    {'provider_name': 'New provider'}, {'provider_type': 'INTERNAL'},
    {'resource_type_code': 'DOC'}, {'resource_type_name_vi': 'New type'},
    {'subject_id': None}, {'grade_level_id': None}, {'uploaded_by': 'user-1'},
    {'title': 'Demo Learning Resource 1'},  # No-op PATCH is still forbidden.
])
def test_source_patch_rejected_without_row_relation_timestamp_change(container, payload):
    owned(container)
    before = snapshot(container)
    with TestClient(create_app(container)) as client:
        response = client.patch('/api/v1/resources/resource-1/metadata', json=payload)
        detail = client.get('/api/v1/resources/resource-1').json()
    assert response.status_code == 409 and response.json()['detail']['code'] == 'RESOURCE_SOURCE_OWNED_READ_ONLY'
    assert 'SOURCE' in response.json()['detail']['message']
    assert detail['source_origin'] == 'POSTGRESQL' and 'source_state_hash' not in detail
    assert snapshot(container) == before


@pytest.mark.parametrize('origin', [None, 'UNKNOWN', 'POSTGRESQL', 'unsupported', 'local'])
def test_domain_fails_closed_for_all_non_local_provenance(origin):
    with pytest.raises(ResourceMetadataOwnershipError):
        assert_resource_metadata_editable({'source_origin': origin})


@pytest.mark.parametrize('origin', ['UNKNOWN', 'unsupported'])
def test_unverified_api_uses_ownership_conflict_not_outage(container, origin):
    owned(container, origin)
    before = snapshot(container)
    with TestClient(create_app(container)) as client:
        response = client.patch('/api/v1/resources/resource-1/metadata', json={'title': 'Changed'})
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'RESOURCE_OWNERSHIP_UNVERIFIED'
    assert snapshot(container) == before


@pytest.mark.parametrize('method,values', [
    ('update_resource_metadata', {'title': 'Changed'}), ('replace_subject_relation', None),
    ('replace_grade_relation', None),
])
def test_repository_blocks_direct_metadata_and_relation_writers(container, method, values):
    owned(container)
    before = snapshot(container)
    with pytest.raises(ResourceMetadataOwnershipError), container.uow() as repo:
        getattr(repo, method)('resource-1', values)
    assert snapshot(container) == before


@pytest.mark.parametrize('payload', [
    {'title': 'Changed', 'subject_id': 'missing'},
    {'title': 'Changed', 'subject_id': 'subject-2', 'grade_level_id': 'grade-1'},
    {'title': 'Changed', 'grade_level_id': 'missing'},
])
def test_local_validation_is_atomic(container, payload):
    owned(container, 'LOCAL')
    before = snapshot(container)
    with TestClient(create_app(container)) as client:
        response = client.patch('/api/v1/resources/resource-1/metadata', json=payload)
    assert response.status_code == 400
    assert snapshot(container) == before


def test_local_success_and_late_database_failure_roll_back_entire_patch(container, monkeypatch):
    owned(container, 'LOCAL')
    with TestClient(create_app(container)) as client:
        response = client.patch('/api/v1/resources/resource-1/metadata', json={
            'title': 'Local title', 'subject_id': 'subject-2', 'grade_level_id': 'grade-2', 'uploaded_by': 'user-2'})
        assert response.status_code == 200, response.text
        assert response.json()['uploaded_by'] == 'user-2'
        assert response.json()['subject_id'] == 'subject-2' and response.json()['grade_level_id'] == 'grade-2'
        before = snapshot(container)
        def fail(*args):
            raise IntegrityError('PRIVATE_SQL', {}, Exception('PRIVATE_CONNECTION'))
        monkeypatch.setattr(SQLAlchemyRepository, 'replace_grade_relation', fail)
        response = client.patch('/api/v1/resources/resource-1/metadata', json={
            'title': 'Must roll back', 'subject_id': 'subject-1', 'grade_level_id': 'grade-1'})
        assert response.status_code == 503 and 'PRIVATE_' not in response.text
        assert snapshot(container) == before
        assert client.patch('/api/v1/resources/absent/metadata', json={'title': 'No row'}).status_code == 404


class SourceReplay:
    source_origin = 'POSTGRESQL'

    def __init__(self, aggregates):
        self.aggregates = aggregates

    def batches(self, stream, cursor, size):
        yield self.aggregates


def test_source_incremental_identity_reservation_replay_and_drift(container):
    owned(container)
    aggregates = deepcopy(next(container.source.batches('resources', None, 100)))
    source = SourceReplay(aggregates)
    sync = MasterSync(source, container.uow)
    with container.uow() as repo:
        before = repo.get('master_learning_resources', 'resource-1')
        repo.reserve_master_mongo_identity('resources', 'resource-1', None, 'resource-1')
        after = repo.get('master_learning_resources', 'resource-1')
        assert after['mongo_id'] == 'resource-1' and after['source_state_hash'] == state_hash(after)
        assert after['updated_at'] == before['updated_at']
    assert sync.run('resources')['resources']['skipped'] == len(aggregates)
    aggregates[0].values.update(title='Source title', publication_status='DRAFT', provider_name='Source provider', updated_at=now())
    aggregates[0].subjects = []
    aggregates[0].grades = []
    assert sync.run('resources')['resources']['updated'] == 1
    with container.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        assert row['title'] == 'Source title' and row['publication_status'] == 'DRAFT'
        assert row['source_state_hash'] == state_hash(row) and row['mongo_id'] == 'resource-1'
        assert repo.find('resource_subjects', {'resource_id': 'resource-1'}) == []
        assert repo.find('resource_grade_levels', {'resource_id': 'resource-1'}) == []
    assert sync.run('resources')['resources']['skipped'] == len(aggregates)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Intentional drift'})
    with pytest.raises(DomainError, match='BLOCKED_LOCAL_SOURCE_CHANGE'):
        sync.run('resources')
    with pytest.raises(DomainError, match='BLOCKED_LOCAL_SOURCE_CHANGE'), container.uow() as repo:
        repo.reserve_master_mongo_identity('resources', 'resource-1', 'resource-1', 'resource-1')


def test_historical_bad_identity_hash_is_not_silently_blessed(container):
    owned(container)
    with container.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        repo.update('master_learning_resources', 'resource-1', {'mongo_id': 'resource-1'})
    with pytest.raises(DomainError, match='BLOCKED_LOCAL_SOURCE_CHANGE'), container.uow() as repo:
        repo.reserve_master_mongo_identity('resources', 'resource-1', 'resource-1', 'resource-1')
    with container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['source_state_hash'] == row['source_state_hash']
