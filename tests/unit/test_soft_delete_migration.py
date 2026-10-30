"""Additive upgrade, safe empty downgrade, and refusal to erase audit history."""
import pytest
from sqlalchemy import inspect, text

from tests.conftest import migrate
from tests.unit.test_resource_soft_delete import deletion


def test_additive_upgrade_preserves_rows_and_guarded_rollback(container):
    c = container
    c.sync.run(full=True)
    with c.uow() as repo:
        original = repo.get('master_learning_resources', 'resource-1')
    fields = {'master_deleted_at', 'master_deleted_by', 'master_deletion_reason', 'deletion_revision'}
    migrate(c.engine, '0016', downgrade=True)
    assert 'resource_deletion_events' not in inspect(c.engine).get_table_names()
    with c.engine.connect() as connection:
        old = dict(connection.execute(text("SELECT * FROM master_learning_resources WHERE id='resource-1'")).mappings().one())
    assert not fields & set(old)
    migrate(c.engine)
    with c.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        assert row == original
        assert row['deletion_revision'] == 0 and row['master_deleted_at'] is None
        repo.update('master_learning_resources', 'resource-1', {'source_origin': 'LOCAL'})
    deletion(c)
    with pytest.raises(RuntimeError, match='SOFT_DELETE_ROLLBACK_REQUIRES'):
        migrate(c.engine, '0016', downgrade=True)
    assert fields <= {column['name'] for column in inspect(c.engine).get_columns('master_learning_resources')}
    # A guarded historical downgrade can already have removed newer nullable
    # columns on SQLite. Verify preserved data against that physical layout.
    with c.engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM resource_deletion_events')) == 1
        assert connection.scalar(text("SELECT master_deleted_at FROM master_learning_resources WHERE id='resource-1'"))
