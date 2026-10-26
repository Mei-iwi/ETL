"""SOURCE is a synthetic test schema and application reader remains read-only."""
import pytest
from sqlalchemy import text

from data_sync_etl.application.sync import MasterSync
from data_sync_etl.security.auth import AuthError
from tests.unit.test_resource_soft_delete import deletion

pytestmark = pytest.mark.postgres


def test_full_incremental_restart_and_older_snapshot_cannot_resurrect(source, pg_container):
    c = pg_container
    sync = MasterSync(source.adapter, c.uow, batch_size=1)
    sync.run(full=True)
    with c.uow() as repo:
        original = repo.get('master_learning_resources', 'resource-1')
    deletion(c)
    sync.run('resources', full=True)
    MasterSync(source.adapter, c.uow, batch_size=1).run('resources')
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE learning_resources SET title='Synthetic newer title' WHERE id='resource-1'"))
    sync.run('resources', full=True)
    with c.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['master_deleted_at']
        assert repo.sync_upsert('master_learning_resources', original, source_origin='POSTGRESQL') == 'SKIPPED'
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE learning_resources SET deleted_at=now(), is_active=false WHERE id='resource-1'"))
    sync.run('resources', full=True)
    with pytest.raises(AuthError, match='RESOURCE_RESTORE_SOURCE_RESTRICTED'):
        deletion(c, restore=True)
