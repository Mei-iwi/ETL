from datetime import timedelta
from uuid import uuid4

import pytest
from bson import BSON
from pymongo import MongoClient
from sqlalchemy import text

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.adapters.source.mongo_metadata import MongoMetadataSourceReader
from data_sync_etl.application.source_mongo_metadata_sync import SourceMongoMetadataSync
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError, now

from .test_file_import_flow import setup_import

pytestmark = pytest.mark.postgres


@pytest.fixture
def dual(source, pg_container, mongo_test_database):
    source_name = 'etl_test_source_' + uuid4().hex
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000, timeoutMS=10000, tz_aware=True)
    db_source, db_target = client[source_name], client[mongo_test_database[1]]
    initialize(db_target, apply=True, dimension=pg_container.settings.vector_dimension)
    stamp = now() - timedelta(days=1)
    db_source.users.insert_one(dict(_id='MUSER-1', subject_id='subject-1', organization_name='QA organization',
        address='QA address', bio='Real source fixture bio', created_at=stamp, updated_at=stamp,
        hashed_password='DO_NOT_COPY', email='DO_NOT_COPY'))
    db_source.learning_resources.insert_one(dict(_id='MRES-1', description='Source description',
        authors=[{'name': 'Author one'}, {'name': 'Author two', 'organization': 'Organization', 'role': 'Editor'}],
        tags=['tag-one', 'tag-two'], created_at=stamp, updated_at=stamp))
    db_source.learning_resources.insert_one(dict(_id='ORPHAN', description='Not Master-backed', created_at=stamp, updated_at=stamp))
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE users SET mongo_id='MUSER-1' WHERE id='user-1'"))
        connection.execute(text("UPDATE learning_resources SET mongo_id='MRES-1' WHERE id='resource-1'"))
    store = MongoMetadataStore(db_target)
    reader = MongoMetadataSourceReader(db_source, db_target)
    sync = SourceMongoMetadataSync(pg_container.uow, reader, store, batch_size=1,
        reference_guard=source.adapter.metadata_identity_references, subject_guard=source.adapter.metadata_subject_exists)
    master = MasterSync(source.adapter, pg_container.uow, batch_size=1, metadata_projection=sync)
    try:
        yield db_source, db_target, sync, master, store, reader
    finally:
        assert source_name.startswith('etl_test_source_') and len(source_name) == 48
        client.drop_database(source_name)
        client.close()


def snapshot(db):
    return {name: [BSON.encode(doc) for doc in db[name].find({}).sort('_id', 1)]
            for name in ('users', 'learning_resources')}


def test_dual_metadata_full_independent_update_null_absence_and_source_readonly(source, pg_container, dual):
    src, dst, sync, master, _, _ = dual
    before = snapshot(src)
    result = master.run(full=True)
    assert result['resources']['failed'] == 0
    user = dst.users.find_one({'_id': 'MUSER-1'})
    resource = dst.learning_resources.find_one({'_id': 'MRES-1'})
    assert user['bio'] == 'Real source fixture bio' and 'hashed_password' not in user and 'email' not in user
    assert len(resource['authors']) == 2 and resource['description'] == 'Source description'
    assert dst.learning_resources.find_one({'_id': 'resource-1'}) is None
    assert dst.learning_resources.find_one({'_id': 'ORPHAN'}) is None
    assert snapshot(src) == before
    assert all(r['updated'] == r['inserted'] == 0 for r in sync.run().values())
    assert dst.learning_resources.find_one({'_id': 'MRES-1'}) == resource
    # Mongo-only update: no PG sync needed. ABSENT authors stays; explicit NULL tags clears.
    src.learning_resources.update_one({'_id': 'MRES-1'}, {'$set': {'description': 'Mongo-only change',
        'tags': None, 'updated_at': now()}, '$unset': {'authors': ''}})
    report = sync.run('resources')['resources']
    updated = dst.learning_resources.find_one({'_id': 'MRES-1'})
    assert report['updated'] == 1 and updated['description'] == 'Mongo-only change' and updated['tags'] is None
    assert updated['authors'] == resource['authors'] and updated['created_at'] == resource['created_at']
    dst.learning_resources.update_one({'_id': 'MRES-1'}, {'$set': {'tags': ['OWNER']}})
    src.learning_resources.update_one({'_id': 'MRES-1'}, {'$set': {'description': 'Changed again', 'updated_at': now()},
        '$unset': {'tags': ''}})
    assert sync.run('resources')['resources']['updated'] == 1
    assert dst.learning_resources.find_one({'_id': 'MRES-1'})['tags'] == ['OWNER']
    with pg_container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['mongo_id'] == 'MRES-1'
        assert repo.get('master_users', 'user-1')['mongo_id'] == 'MUSER-1'


def test_metadata_outage_does_not_rollback_master_and_full_retry_recovers(source, pg_container, dual, monkeypatch):
    src, dst, sync, master, store, _ = dual
    stamp = now()
    src.learning_resources.insert_one(dict(_id='MRES-2', description='Second source document', created_at=stamp, updated_at=stamp))
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE learning_resources SET mongo_id='MRES-2' WHERE id='resource-2'"))
    original = store.sync_source_metadata

    def outage(stream, document):
        if document.id == 'MRES-2':
            raise ConnectionError('Simulated isolated destination outage')
        return original(stream, document)

    monkeypatch.setattr(store, 'sync_source_metadata', outage)
    result = master.run(full=True)
    assert result['resources']['failed'] == 0
    report = sync.run('resources')['resources']
    assert report['status'] == 'FAILED' and report['error_code'] == 'TARGET_MONGO_WRITE_FAILED'
    assert report['checkpoint'] == 'resource-1'
    with pg_container.uow() as repo:
        assert all(run['status'] == 'COMPLETED' for run in repo.find('sync_runs', {}))
    monkeypatch.setattr(store, 'sync_source_metadata', original)
    retry = sync.run('resources')['resources']
    assert retry['failed'] == 0 and retry['inserted'] == 1 and retry['unchanged'] == 1
    assert dst.learning_resources.count_documents({}) == 2


def test_stale_target_subject_conflict_same_database_and_identity_upgrade(dual):
    src, dst, sync, master, _, reader = dual
    master.run(full=True)
    before = dst.users.find_one({'_id': 'MUSER-1'})
    src.users.update_one({'_id': 'MUSER-1'}, {'$set': {'subject_id': 'missing-subject', 'updated_at': now()}})
    assert sync.run('users')['users']['conflicts'] == 1
    assert dst.users.find_one({'_id': 'MUSER-1'}) == before
    src.users.update_one({'_id': 'MUSER-1'}, {'$set': {'subject_id': 'subject-1'}})
    dst.users.update_one({'_id': 'MUSER-1'}, {'$set': {'updated_at': now()+timedelta(days=1), 'bio': 'Newer target'}})
    assert sync.run('users')['users']['skipped_stale'] == 1
    assert dst.users.find_one({'_id': 'MUSER-1'})['bio'] == 'Newer target'
    reader.destination = reader.database
    before_source = snapshot(src)
    assert sync.run('users')['users']['error_code'] == 'SOURCE_MONGO_SAME_DESTINATION'
    assert snapshot(src) == before_source
    reader.destination = dst
    # Explicit legacy identity document may be enriched, but created_at stays owned by target.
    resource = dst.learning_resources.find_one({'_id': 'MRES-1'})
    dst.learning_resources.replace_one({'_id': 'MRES-1'}, {k: resource[k] for k in ('_id','created_at','updated_at')})
    assert sync.run('resources')['resources']['updated'] == 1
    assert dst.learning_resources.find_one({'_id': 'MRES-1'})['created_at'] == resource['created_at']
    with pytest.raises(DomainError):
        MongoMetadataSourceReader(dst, dst).assert_distinct_destination()


def test_dual_source_to_native_pdf_ocr_and_matching_content_units_without_bag_writes(source, pg_container, dual, tmp_path):
    src, dst, sync, _, _, _ = dual
    c = pg_container
    original_file = setup_import(source, c, tmp_path)
    c.sync.metadata_projection = sync
    c.sync.run(full=True)
    stamp = now()
    sentinel = dict(_id='old-bag', resource_version_id='old-version', unit_type='SECTION',
        keywords=['keep'], embedding_text='keep', embedding=[1.0]+[0.0]*(c.settings.vector_dimension-1),
        embedding_model='existing-fixture-not-generated', created_at=stamp, updated_at=stamp)
    dst.content_bags.insert_one(sentinel)
    bag_before = BSON.encode(dst.content_bags.find_one({'_id': 'old-bag'}))
    source_before = snapshot(src)
    c.mongo_projector.documents = MongoContentStore(dst, invalidate_content_bags=False)
    assert c.source_file_worker.once('native-import')
    for _ in range(3):
        if not c.worker.once('native-ocr'):
            break
    with c.uow() as repo:
        version = repo.find('resource_versions', {})[0]
        job = repo.find('ocr_jobs', {})[0]
        units = repo.find('content_units', {'resource_version_id': version['id']})
        assert job['status'] == 'COMPLETED' and units
        assert all(u['mongo_id'] == u['id'] and u['unit_type'] == 'PAGE_TEXT' for u in units)
        for unit in units:
            projected = dst.mongo_content_units.find_one({'_id': unit['id']})
            assert projected['text'] == repo.get('content_unit_texts', unit['id'])['text']
            assert projected['resource_version_id'] == version['id'] and projected['is_current']
            assert projected['keywords'] == []
    page = dst.ocr_pages.find_one({'job_id': job['id']})
    assert 'genuine native text' in page['raw_text'] and page['char_count'] == len(page['raw_text'])
    assert page['engine_name'] == 'native_pdf_text' and page['source_checksum'] == version['source_hash']
    assert dst.mongo_content_units.count_documents({'resource_version_id': version['id'], 'is_current': True}) == len(units)
    assert BSON.encode(dst.content_bags.find_one({'_id': 'old-bag'})) == bag_before
    assert snapshot(src) == source_before and original_file.exists()
    # A projection retry is idempotent; the prior indexing stage remains untouched.
    c.mongo_projector.project(version['id'], job['id'], generation=1)
    assert BSON.encode(dst.content_bags.find_one({'_id': 'old-bag'})) == bag_before


def test_composition_prioritizes_source_over_identity_bootstrap(source, pg_container, dual, mongo_test_database, monkeypatch):
    src, dst, _, _, _, _ = dual
    monkeypatch.setattr('data_sync_etl.composition.create_source', lambda _: source.adapter)
    settings = Settings(_env_file=None, source_adapter='postgresql', source_mongo_enabled=True,
        source_mongo_database=src.name, mongo_database=dst.name, mongo_uri=mongo_test_database[0],
        master_mongo_identity_bootstrap=True, master_mongo_identity_scope='source_verified',
        storage_root=pg_container.storage.root)
    runtime = Container(settings, pg_container.engine)
    try:
        assert runtime.master_metadata_projection is runtime.mongo_metadata_sync
        assert not runtime.documents.invalidate_content_bags
        runtime.sync.run(full=True)
        assert dst.learning_resources.find_one({'_id': 'MRES-1'})['description'] == 'Source description'
        assert dst.learning_resources.find_one({'_id': 'resource-2'}) is None
        assert dst.users.find_one({'_id': 'user-2'}) is None
    finally:
        runtime.close()


def test_source_and_target_metadata_rpc_have_no_open_pg_transaction(pg_container, dual, monkeypatch):
    _, _, _, master, store, reader = dual
    calls = []

    def checked(method):
        def invoke(*args):
            with pg_container.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                assert connection.scalar(text(
                    'SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() '
                    'AND pid<>pg_backend_pid() AND xact_start IS NOT NULL')) == 0
            calls.append(method.__name__)
            return method(*args)
        return invoke

    for obj, name in [(reader, 'get_user'), (reader, 'get_resource'), (store, 'sync_source_metadata')]:
        monkeypatch.setattr(obj, name, checked(getattr(obj, name)))
    master.run(full=True)
    assert {'get_user', 'get_resource', 'sync_source_metadata'} <= set(calls)
