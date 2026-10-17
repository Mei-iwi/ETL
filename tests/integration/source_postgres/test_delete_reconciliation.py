from contextlib import contextmanager

import pytest
from bson import BSON
from pymongo import MongoClient
from sqlalchemy import text

from data_sync_etl.application.reconcile_master import ReconcileMaster
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.domain.core import DomainError
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient

pytestmark = pytest.mark.postgres


def setup(source, target):
    target.sync = MasterSync(source.adapter, target.uow, batch_size=1)
    target.sync.run(full=True)
    return ReconcileMaster(source.adapter, target.uow, batch_size=1, max_ratio=0.75)


def remove_fixture_row(source, table, key):
    assert table in {'learning_resources', 'users', 'subjects', 'grade_levels', 'roles'}
    with source.engine.begin() as connection:
        assert connection.scalar(text('SELECT current_schema()')) == source.schema
        assert source.schema.startswith('etl_source_test_') and 'test' in source.database.split('_')
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text(f'DELETE FROM "{source.schema}"."{table}" WHERE id=:id'), {'id': key})


@pytest.mark.parametrize('stream,table,key,active,deleted', [
    ('resources', 'learning_resources', 'resource-3', 'is_active', 'deleted_at'),
    ('users', 'users', 'user-2', 'is_active', 'deleted_at'),
    ('subjects', 'subjects', 'subject-2', 'is_active', 'deleted_at'),
    ('grades', 'grade_levels', 'grade-2', 'grade_is_active', 'grade_deleted_at'),
])
def test_hard_delete_preview_approved_idempotent_and_history_preserved(source, pg_container, stream, table, key, active, deleted):
    service = setup(source, pg_container)
    target_table = {'resources': 'master_learning_resources', 'users': 'master_users', 'subjects': 'subjects', 'grades': 'master_grade_levels'}[stream]
    with source.engine.connect() as connection:
        original_source = dict(connection.execute(text(f'SELECT * FROM {table} WHERE id=:id'), {'id': key}).mappings().one())
    remove_fixture_row(source, table, key)
    with pg_container.uow() as repo:
        before = repo.get(target_table, key)
        history = {name: repo.find(name) for name in ('resource_versions', 'ocr_jobs', 'content_units', 'resource_subjects', 'resource_grade_levels')}
    pg_container.sync.run(stream, full=False)
    pg_container.sync.run(stream, full=True)
    plan = service.run(stream)
    assert plan['status'] == 'RECONCILIATION_PREVIEW', plan
    assert plan['streams'][stream]['missing_candidate_count'] == 1
    with pg_container.uow() as repo:
        assert repo.get(target_table, key) == before
    assert service.run(stream, apply=True, fingerprint='wrong')['error_code'] == 'BLOCKED_PLAN_CHANGED'
    result = service.run(stream, apply=True, fingerprint=plan['fingerprint'])
    assert result['status'] == 'RECONCILIATION_APPLIED' and result['confirmed'] == 1, result
    with pg_container.uow() as repo:
        row = repo.get(target_table, key)
        assert not row[active] and row[deleted] == row['source_missing_at']
        assert row['source_origin'] == 'POSTGRESQL' and row['id'] == before['id']
        assert all(repo.find(name) == rows for name, rows in history.items())
    again = service.run(stream)
    assert service.run(stream, apply=True, fingerprint=again['fingerprint'])['confirmed'] == 0
    with pg_container.uow() as repo:
        assert repo.get(target_table, key) == row
    with source.engine.begin() as connection:
        names = list(original_source)
        connection.execute(text(f'INSERT INTO {table} ('+','.join(names)+') VALUES ('+','.join(':'+name for name in names)+')'), original_source)
    pg_container.sync.run(stream, full=True)
    with pg_container.uow() as repo:
        restored = repo.get(target_table, key)
        assert restored[active] and restored[deleted] is None and restored['source_missing_at'] is None
        assert restored['source_origin'] == 'POSTGRESQL'


@pytest.mark.parametrize('full', [False, True])
def test_soft_delete_and_restore_keep_ownership_and_grade_prefix(source, pg_container, full):
    setup(source, pg_container)
    for table, key in [('users', 'user-1'), ('learning_resources', 'resource-1'), ('subjects', 'subject-1'), ('grade_levels', 'grade-1')]:
        with source.engine.begin() as connection:
            deletion = '' if table == 'users' else ', deleted_at=clock_timestamp()'
            connection.execute(text(f'UPDATE {table} SET is_active=false{deletion} WHERE id=:id'), {'id': key})
    pg_container.sync.run(full=full)
    for table, key in [('master_users', 'user-1'), ('master_learning_resources', 'resource-1'), ('subjects', 'subject-1'), ('master_grade_levels', 'grade-1')]:
        with pg_container.uow() as repo:
            row = repo.get(table, key)
            prefix = 'grade_' if table == 'master_grade_levels' else ''
            assert not row[prefix+'is_active']
            assert (row[prefix+'deleted_at'] is not None) == (table != 'master_users')
    for table, key in [('users', 'user-1'), ('learning_resources', 'resource-1'), ('subjects', 'subject-1'), ('grade_levels', 'grade-1')]:
        with source.engine.begin() as connection:
            deletion = '' if table == 'users' else ', deleted_at=NULL'
            connection.execute(text(f'UPDATE {table} SET is_active=true{deletion} WHERE id=:id'), {'id': key})
    pg_container.sync.run(full=full)
    with pg_container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['is_active']
        assert repo.get('master_grade_levels', 'grade-1')['grade_is_active']


def test_unknown_local_never_claimed_empty_threshold_stale_and_dependency_blocked(source, pg_container):
    service = setup(source, pg_container)
    with pg_container.uow() as repo:
        original = repo.get('master_learning_resources', 'resource-1')
        for key, origin in [('local-only', 'LOCAL'), ('unknown-only', 'UNKNOWN')]:
            repo.insert('master_learning_resources', {**original, 'id': key, 'source_origin': origin, 'source_state_hash': None})
    plan = service.run('resources')
    assert plan['streams']['resources']['local_excluded'] == plan['streams']['resources']['unknown_excluded'] == 1
    with pg_container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'source_origin': 'LOCAL'})
    with pytest.raises(DomainError, match='BLOCKED_SOURCE_OWNERSHIP'):
        pg_container.sync.run('resources', full=True)
    remove_fixture_row(source, 'roles', 'role-1')
    assert service.run('users')['error_code'] == 'BLOCKED_DEPENDENCY'


def test_reappear_timeout_and_mass_change_never_apply(source, pg_container, monkeypatch):
    service = setup(source, pg_container)
    with source.engine.connect() as connection:
        old = dict(connection.execute(text("SELECT * FROM learning_resources WHERE id='resource-3'")).mappings().one())
    remove_fixture_row(source, 'learning_resources', 'resource-3')
    plan = service.run('resources')
    strict = ReconcileMaster(source.adapter, pg_container.uow, max_ratio=0.1)
    assert strict.run('resources')['status'] == 'BLOCKED'
    with source.engine.begin() as connection:
        names = list(old)
        connection.execute(text('INSERT INTO learning_resources ('+','.join(names)+') VALUES ('+','.join(':'+name for name in names)+')'), old)
    assert service.run('resources', apply=True, fingerprint=plan['fingerprint'])['status'] == 'BLOCKED'
    pg_container.sync.run('resources', full=True)
    with pg_container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-3')['is_active']
    @contextmanager
    def interrupted():
        raise ConnectionError('PRIVATE_SOURCE_CONNECTION')
        yield
    monkeypatch.setattr(source.adapter, 'snapshot', interrupted)
    assert service.run('resources', apply=True, fingerprint=plan['fingerprint'])['error_code'] == 'BLOCKED_SNAPSHOT_OR_SCHEMA'


def test_mongo_unchanged_and_api_gate_after_tombstone(source, pg_container, mongo_test_database):
    pg_container.settings.admin_enabled = True
    service = setup(source, pg_container)
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000)
    db = client[mongo_test_database[1]]
    try:
        for name in ('users', 'learning_resources', 'ocr_pages', 'mongo_content_units', 'content_bags'):
            db[name].insert_one({'_id': 'sentinel', 'untouched': True})
        before = {name: BSON.encode(db[name].find_one({'_id': 'sentinel'})) for name in db.list_collection_names()}
        remove_fixture_row(source, 'learning_resources', 'resource-3')
        plan = service.run('resources')
        assert service.run('resources', apply=True, fingerprint=plan['fingerprint'])['confirmed'] == 1
        with TestClient(create_app(pg_container)) as api:
            assert api.get('/api/v1/resources/resource-3').status_code == 404
            assert api.get('/api/v1/resources/resource-3/versions').status_code == 404
        assert all(BSON.encode(db[name].find_one({'_id': 'sentinel'})) == body for name, body in before.items())
    finally:
        client.close()
