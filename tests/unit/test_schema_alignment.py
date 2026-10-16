from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from conftest import ingest, migrate
from pydantic import ValidationError
from sqlalchemy import inspect

from data_sync_etl.adapters.mongo.foundation_store import MongoContentReader
from data_sync_etl.adapters.source.postgresql import SnapshotReader
from data_sync_etl.application.leaf_keywords import SaveLeafKeywords
from data_sync_etl.db.models import MasterLearningResource
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import ContentBagDocument, ContentUnitDocument


def test_diagram_reference_migration_preserves_rows_and_no_backfill(container):
    migrate(container.engine, '0013', downgrade=True)
    # Source rows use dictionaries, not the current ORM on a historical schema.
    from sqlalchemy import text
    with container.engine.begin() as connection:
        connection.execute(text("INSERT INTO master_grade_levels (id,education_level_id,grade_code,grade_name_vi,grade_is_active,education_code,education_name_vi,education_is_active) VALUES ('QA26_GRADE','EDU','G','Grade',true,'E','Education',true)"))
    migrate(container.engine)
    migrate(container.engine)
    inspector = inspect(container.engine)
    for table, name, length in [('master_grade_levels', 'grade_mongo_id', 24),
                                ('resource_versions', 'mongo_id', 30), ('content_units', 'mongo_id', 30)]:
        column = next(c for c in inspector.get_columns(table) if c['name'] == name)
        assert column['nullable'] and column['type'].length == length and column['default'] is None
        assert not any(name in f['constrained_columns'] for f in inspector.get_foreign_keys(table))
        if name == 'mongo_id':
            assert any(i['unique'] and i['column_names'] == [name] for i in inspector.get_indexes(table))
    with container.uow() as repo:
        grade = repo.get('master_grade_levels', 'QA26_GRADE')
        assert grade['grade_mongo_id'] is None and grade['grade_name_vi'] == 'Grade'
        repo.update('master_grade_levels', grade['id'], {'grade_mongo_id': 'a' * 24})
    with pytest.raises(RuntimeError, match='mappings exist'):
        migrate(container.engine, '0013', downgrade=True)


def test_grade_reference_presence_length_and_sticky_null(container):
    values = dict(id='QA26_GRADE', education_level_id='education-1', code='G', name_vi='Grade',
                  created_at=now(), updated_at=now(), mongo_id='a' * 24)
    assert SnapshotReader.record('grades', values).mongo_id == 'a' * 24
    for bad in ('a' * 25, '', ' bad ', 123):
        with pytest.raises(ValueError):
            SnapshotReader.record('grades', {**values, 'mongo_id': bad})
    container.sync.run(full=True)
    with container.uow() as repo:
        grade = repo.find('master_grade_levels', {}, limit=1)[0]
        repo.sync_upsert('master_grade_levels', {'id': grade['id'], 'grade_mongo_id': 'a' * 24})
        repo.sync_upsert('master_grade_levels', {'id': grade['id'], 'grade_mongo_id': None})
        assert repo.get('master_grade_levels', grade['id'])['grade_mongo_id'] == 'a' * 24


@pytest.mark.parametrize('count', [1, 100, 1001])
def test_metadata_keyset_exact_coverage_at_page_boundaries(container, count):
    ids = [f'QA26_META_{i:04d}' for i in range(count)]
    with container.uow() as repo:
        repo.session.execute(MasterLearningResource.__table__.insert(), [
            dict(id=identifier, resource_type_id='type', resource_type_code='TYPE',
                 resource_type_name_vi='Type', resource_type_is_active=True,
                 publication_status='DRAFT', is_active=True) for identifier in ids])
    seen, after = [], None
    while True:
        with container.uow() as repo:
            rows = repo.master_metadata_page('resources', after, 100)
        if not rows:
            break
        assert all(set(row) == {'id', 'mongo_id', 'created_at', 'updated_at', 'source_origin',
                                'is_active', 'deleted_at', 'publication_status'} for row in rows)
        seen.extend(row['id'] for row in rows)
        after = rows[-1]['id']
    assert seen == ids and len(set(seen)) == count


def test_content_projection_links_only_after_success_and_keeps_target_ids(container, monkeypatch):
    version = ingest(container)
    job = container.ocr.create_job(version['id'])
    original = container.documents.upsert_content_unit
    monkeypatch.setattr(container.documents, 'upsert_content_unit', MagicMock(side_effect=ConnectionError('test outage')))
    for _ in range(3):
        container.worker.once('qa-worker')
    with container.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': version['id']})
        assert units and all(unit['mongo_id'] is None for unit in units)
        assert repo.get('resource_versions', version['id'])['mongo_id'] is None
    monkeypatch.setattr(container.documents, 'upsert_content_unit', original)
    container.mongo_projector.project(version['id'], job['id'])
    with container.uow() as repo:
        linked = repo.find('content_units', {'resource_version_id': version['id']})
        assert [u['id'] for u in linked] == [u['id'] for u in units]
        assert all(u['mongo_id'] == u['id'] and u['id'] in container.documents.units for u in linked)
    container.mongo_projector.project(version['id'], job['id'])
    assert len(container.documents.units) == len(units)
    with container.uow() as repo:
        repo.update('content_units', units[0]['id'], {'mongo_id': 'foreign-target'})
    with pytest.raises(DomainError, match='BLOCKED_MONGO_ID_MAPPING'):
        container.mongo_projector.project(version['id'], job['id'])


def test_excel_leaf_limits_and_keyword_rpc_outside_pg_transaction():
    stamp = now()
    data = dict(_id='leaf', resource_version_id='version', text='text', markdown='text',
                created_at=stamp, updated_at=stamp)
    assert len(ContentUnitDocument(**data, keywords=[str(i) for i in range(5)]).keywords) == 5
    for change in ({'keywords': [str(i) for i in range(6)]}, {'title': 'a' * 501}):
        with pytest.raises(ValidationError):
            ContentUnitDocument(**data, **change)
    bag = dict(_id='root', resource_version_id='version', created_at=stamp, updated_at=stamp,
               unit_type='SECTION', keywords=['one'], embedding_text='one', embedding=[1.0], embedding_model='m')
    for change in ({'title': 'a' * 501}, {'embedding_model': 'a' * 256}):
        with pytest.raises(ValidationError):
            ContentBagDocument(**{**bag, **change})
    active = False

    @contextmanager
    def scope():
        nonlocal active
        active = True
        repo = MagicMock()
        repo.get.return_value = {'resource_version_id': 'version'}
        repo.find.return_value = []
        yield repo
        active = False

    def get_unit(_):
        assert not active
        return ContentUnitDocument(**data)

    def update(*_):
        assert not active

    documents = MagicMock()
    documents.get_unit.side_effect = get_unit
    documents.update_leaf_keywords.side_effect = update
    SaveLeafKeywords(scope, documents).execute('leaf', ['one'])


def test_invalid_leaf_keywords_do_not_invalidate_bags_or_write(monkeypatch):
    invalidate = MagicMock()
    monkeypatch.setattr('data_sync_etl.adapters.mongo.foundation_store.invalidate_bags', invalidate)
    database = MagicMock()
    reader = MongoContentReader(database)
    unit = ContentUnitDocument(_id='leaf', resource_version_id='version', text='text', markdown='text',
                               created_at=now(), updated_at=now())
    with pytest.raises(DomainError):
        reader.update_leaf_keywords(unit, [str(i) for i in range(6)])
    invalidate.assert_not_called()
    reader.database.mongo_content_units.update_one.assert_not_called()
