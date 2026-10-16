import pytest
from fastapi.testclient import TestClient

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ownership import state_hash
from data_sync_etl.main import create_app

from .test_source import execute, use_source

pytestmark = pytest.mark.postgres


def test_real_source_patch_readonly_incremental_bootstrap_and_drift(source, pg_container):
    c = pg_container
    use_source(c, source)
    c.sync.run(full=True)
    with c.uow() as repo:
        before = repo.get('master_learning_resources', 'resource-1')
        relations = repo.find('resource_subjects', {'resource_id': 'resource-1'})
    with TestClient(create_app(c)) as client:
        response = client.patch('/api/v1/resources/resource-1/metadata', json={'title': 'Blocked'})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'RESOURCE_SOURCE_OWNED_READ_ONLY'
    with c.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1') == before
        assert repo.find('resource_subjects', {'resource_id': 'resource-1'}) == relations
        repo.reserve_master_mongo_identity('resources', 'resource-1', before['mongo_id'], before['mongo_id'] or 'resource-1')
        row = repo.get('master_learning_resources', 'resource-1')
        assert row['source_state_hash'] == state_hash(row)
    execute(source, "UPDATE learning_resources SET title='SOURCE approved edit',publication_status='DRAFT' WHERE id='resource-1'")
    c.sync.run('resources')
    with c.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        assert row['title'] == 'SOURCE approved edit' and row['source_state_hash'] == state_hash(row)
    assert c.sync.run('resources')['resources']['updated'] == 0
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Intentional drift'})
    with pytest.raises(DomainError, match='BLOCKED_LOCAL_SOURCE_CHANGE'):
        c.sync.run('resources')
