import json
from datetime import UTC, datetime

import pytest
from conftest import migrate
from sqlalchemy import inspect
from test_master_metadata_projection import MetadataMemory

from data_sync_etl.adapters.source.postgresql import SnapshotReader
from data_sync_etl.application.master_metadata_projection import MasterMetadataProjection
from data_sync_etl.db.models import MODELS
from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.source import LearningResource, User


def test_nullable_unique_references_and_other_ten_tables_unchanged(container):
    inspector = inspect(container.engine)
    counts = {'master_learning_resources': 29, 'master_users': 23, 'master_grade_levels': 24,
              'subjects': 12, 'resource_subjects': 3, 'resource_grade_levels': 2,
              'resource_versions': 20, 'content_units': 13, 'ocr_jobs': 12, 'ocr_page_tasks': 14}
    for table, count in counts.items():
        assert len(inspector.get_columns(table)) == len(MODELS[table].__table__.columns) == count
    for table in ('master_users', 'master_learning_resources'):
        column = next(c for c in inspector.get_columns(table) if c['name'] == 'mongo_id')
        assert column['nullable'] and column['type'].length == 30
        assert any(i['unique'] and i['column_names'] == ['mongo_id'] for i in inspector.get_indexes(table))
        assert not any('mongo_id' in f['constrained_columns'] for f in inspector.get_foreign_keys(table))


def test_migration_preserves_old_rows_no_backfill_and_guards_downgrade(container):
    migrate(container.engine, '0012', downgrade=True)
    # Old schema records, inserted without using the now-new ORM.
    from sqlalchemy import text
    with container.engine.begin() as c:
        c.execute(text("INSERT INTO master_users (id,created_at,updated_at,email,hashed_password,role_id,role_code,role_name,role_is_active,is_active) VALUES ('QA26_OLD',:time,:time,'fixture-only','private-fixture','r','R','Role',true,true)"),
                  {'time': datetime(2026, 1, 1, tzinfo=UTC).isoformat()})
    migrate(container.engine)
    migrate(container.engine)
    with container.uow() as repo:
        before = repo.get('master_users', 'QA26_OLD')
        assert before['mongo_id'] is None
        repo.reserve_master_mongo_identity('users', 'QA26_OLD', None, 'MDOC-QA26')
    with pytest.raises(RuntimeError, match='mappings exist'):
        migrate(container.engine, '0012', downgrade=True)
    with container.uow() as repo:
        after = repo.get('master_users', 'QA26_OLD')
        assert {k: v for k, v in before.items() if k != 'mongo_id'} == {k: v for k, v in after.items() if k != 'mongo_id'}


@pytest.mark.parametrize('name,model', [('users', User), ('resources', LearningResource)])
def test_source_preserves_nonpk_reference_and_validates(name, model):
    values = {'id': 'SRC-123', 'mongo_id': 'MDOC-123', 'created_at': datetime.now(UTC), 'updated_at': datetime.now(UTC)}
    values.update({'email': 'test-only', 'hashed_password': 'private-fixture', 'role_id': 'role', 'full_name': None}
                  if name == 'users' else {'provider_id': None, 'resource_type_id': 'type', 'title': None, 'publication_status': 'DRAFT'})
    assert SnapshotReader.record(name, values).mongo_id == 'MDOC-123'
    for invalid in ('', ' ', ' bad ', 'a' * 31, 123):
        with pytest.raises(ValueError):
            model.model_validate({**values, 'mongo_id': invalid})


def test_sync_reference_nonnull_sticky_null_absence_remap_and_duplicates(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        original = repo.get('master_learning_resources', 'resource-1')
        repo.sync_upsert('master_learning_resources', {'id': 'resource-1', 'mongo_id': 'MDOC-1'})
        repo.sync_upsert('master_learning_resources', {'id': 'resource-1', 'mongo_id': None})
        repo.sync_upsert('master_learning_resources', {'id': 'resource-1', 'title': original['title']})
        assert repo.get('master_learning_resources', 'resource-1')['mongo_id'] == 'MDOC-1'
    for values in ({'id': 'resource-1', 'mongo_id': 'MDOC-2'}, {'id': 'resource-2', 'mongo_id': 'MDOC-1'}):
        with pytest.raises(DomainError, match='BLOCKED_MONGO_ID_MAPPING'), container.uow() as repo:
            repo.sync_upsert('master_learning_resources', values)
    # Existing source fixtures omit the field: Full/Incremental must not erase the reference.
    container.sync.run('resources', full=True)
    container.sync.run('resources')
    with container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['mongo_id'] == 'MDOC-1'


def test_json_field_presence_and_explicit_null_preserve_mapping(container):
    path = container.source.root / 'resources.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data[0]['mongo_id'] = 'MDOC-FIXTURE'
    path.write_text(json.dumps(data), encoding='utf-8')
    container.sync.run(full=True)
    data[0]['mongo_id'] = None
    path.write_text(json.dumps(data), encoding='utf-8')
    container.sync.run('resources', full=True)
    with container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['mongo_id'] == 'MDOC-FIXTURE'


def test_source_verified_nonpk_and_null_fallback_excludes_master_only(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.sync_upsert('master_learning_resources', {'id': 'resource-1', 'mongo_id': 'MDOC-123'}, source_origin='POSTGRESQL')
    store = MetadataMemory()
    projection = MasterMetadataProjection(container.uow, store, identity_bootstrap=True,
                                          identity_scope='source_verified', batch_size=2,
                                          identity_guard=lambda stream, ids: {k: 'MDOC-123' if k == 'resource-1' else None
                                                                              for k in ids if k != 'resource-3'})
    result = projection.run('resources')['resources']
    assert result['projected'] == 2 and result['skipped_not_source_backed'] == 1
    assert set(store.resources) == {'MDOC-123', 'resource-2'}
    assert result['scanned'] == sum(result[k] for k in ('projected', 'unchanged', 'skipped_not_source_backed', 'skipped_no_extension', 'skipped_unapproved', 'conflicts', 'failed'))
    with container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-2')['mongo_id'] == 'resource-2'
        assert repo.get('master_learning_resources', 'resource-3')['mongo_id'] is None
    repeated = projection.run('resources')['resources']
    assert repeated['unchanged'] == 2 and repeated['projected'] == 0


def test_source_verified_without_guard_fail_closed_and_cas_conflict(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    store = MetadataMemory()
    report = MasterMetadataProjection(container.uow, store, identity_bootstrap=True,
                                      identity_scope='source_verified').run('users')['users']
    assert report['status'] == 'BLOCKED_MONGO_ID_MAPPING' and not store.users
    with container.uow() as repo:
        repo.reserve_master_mongo_identity('users', 'user-1', None, 'MDOC-X')
    with pytest.raises(DomainError), container.uow() as repo:
        repo.reserve_master_mongo_identity('users', 'user-1', None, 'MDOC-Y')
