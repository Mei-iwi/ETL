from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from bson import ObjectId
from pymongo import MongoClient
from sqlalchemy import text

from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.application.auto_sync import AutoSyncCoordinator
from data_sync_etl.application.master_metadata_projection import MasterMetadataProjection
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import UserProfileDocument
from data_sync_etl.workers.master_sync import MasterSyncWorker

pytestmark = pytest.mark.postgres


@pytest.fixture
def projection_runtime(source, pg_container, mongo_test_database):
    uri, name = mongo_test_database
    client = MongoClient(uri, serverSelectionTimeoutMS=3000, timeoutMS=10000, tz_aware=True)
    try:
        db = client[name]
        initialize(db, apply=True, dimension=pg_container.settings.vector_dimension)
        store = MongoMetadataStore(db)
        with source.engine.connect() as conn:
            ids = {stream: list(conn.execute(text('SELECT id FROM ' + table)).scalars())
                   for stream, table in [('users', 'users'), ('resources', 'learning_resources')]}
        projection = MasterMetadataProjection(
            pg_container.uow, store, batch_size=1, identity_bootstrap=True, identity_ids=ids,
            identity_guard=source.adapter.metadata_identity_conflicts,
        )
        sync = MasterSync(source.adapter, pg_container.uow, batch_size=1, metadata_projection=projection)
        yield db, store, projection, sync
    finally:
        client.close()


def test_real_source_master_mongo_idempotent_preserve_extensions(source, pg_container, projection_runtime):
    db, store, projection, sync = projection_runtime
    stamp = now() - timedelta(days=1)
    db.users.insert_one({'_id': 'user-1', 'created_at': stamp, 'updated_at': stamp,
                        'bio': 'Manually managed', 'address': 'Owner-managed address'})
    db.learning_resources.insert_one({'_id': 'resource-1', 'created_at': stamp, 'updated_at': stamp,
                                     'authors': [{'name': 'Author'}], 'tags': ['toán'], 'description': 'Independent'})
    before_user = db.users.find_one({'_id': 'user-1'})
    before_resource = db.learning_resources.find_one({'_id': 'resource-1'})
    result = sync.run(full=True)
    assert result['resources']['inserted'] == 3
    counts = {name: db[name].count_documents({}) for name in ('users', 'learning_resources')}
    assert counts['learning_resources'] == 3
    assert db.users.find_one({'_id': 'user-1'}) == before_user
    assert db.learning_resources.find_one({'_id': 'resource-1'}) == before_resource
    report = projection.run()
    assert all(r['projected'] == 0 and r['unchanged'] == r['scanned'] for r in report.values())
    sync.run(full=False)
    assert {name: db[name].count_documents({}) for name in counts} == counts
    for doc in db.users.find({'_id': {'$ne': 'user-1'}}):
        assert set(doc) == {'_id', 'created_at', 'updated_at'}
    with source.engine.begin() as conn:
        conn.execute(text("UPDATE learning_resources SET is_active=false, deleted_at=now() WHERE id='resource-1'"))
    sync.run('resources')
    assert db.learning_resources.find_one({'_id': 'resource-1'}) == before_resource
    with pg_container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['is_active'] is False
    assert all(db[name].count_documents({}) == 0 for name in ('ocr_pages', 'mongo_content_units', 'content_bags'))


def test_real_mapping_conflict_no_duplicate_and_safe_records_continue(source, projection_runtime):
    db, store, projection, sync = projection_runtime
    with source.engine.begin() as conn:
        conn.execute(text("UPDATE learning_resources SET mongo_id='old-external-id' WHERE id='resource-1'"))
    stamp = now()
    db.learning_resources.insert_one({'_id': 'old-external-id', 'created_at': stamp, 'updated_at': stamp, 'tags': ['old']})
    before = db.learning_resources.find_one({'_id': 'old-external-id'})
    sync.run(full=True)
    report = projection.run('resources')['resources']
    assert report['status'] == 'BLOCKED_MONGO_ID_MAPPING' and report['conflicts'] == 1
    assert report['unchanged'] == 2
    assert db.learning_resources.find_one({'_id': 'resource-1'}) is None
    assert db.learning_resources.find_one({'_id': 'old-external-id'}) == before


def test_real_outage_partial_batch_reconcile_after_master_commit(pg_container, projection_runtime, monkeypatch):
    db, store, projection, sync = projection_runtime
    original = store.ensure_resource_identity
    calls = 0

    def interrupted(document):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConnectionError('Simulated Mongo outage after first persisted document')
        return original(document)

    monkeypatch.setattr(store, 'ensure_resource_identity', interrupted)
    result = sync.run(full=True)
    assert result['resources']['inserted'] == 3
    with pg_container.uow() as repo:
        assert repo.find('sync_runs', {'stream_name': 'resources'})[0]['status'] == 'COMPLETED'
    assert db.learning_resources.count_documents({}) == 1
    monkeypatch.setattr(store, 'ensure_resource_identity', original)
    report = projection.run('resources')['resources']
    assert report['projected'] == 2 and report['unchanged'] == 1
    assert db.learning_resources.count_documents({}) == 3


def test_real_atomic_identity_initializers_preserve_existing_dates(projection_runtime):
    db, store, projection, sync = projection_runtime
    doc = UserProfileDocument(_id='race-user', created_at=now(), updated_at=now())
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.ensure_user_identity(doc), range(8)))
    assert sum(results) == 1 and db.users.count_documents({'_id': 'race-user'}) == 1
    before = db.users.find_one({'_id': 'race-user'})
    assert not store.ensure_user_identity(doc.model_copy(update={'created_at': now(), 'updated_at': now()}))
    assert db.users.find_one({'_id': 'race-user'}) == before


def test_real_auto_reconciles_missed_documents_without_source_changes(source, pg_container, projection_runtime, monkeypatch):
    db, store, projection, sync = projection_runtime
    original = store.ensure_resource_identity

    def unavailable(document):
        raise ConnectionError('Simulated metadata outage during Full baseline')

    monkeypatch.setattr(store, 'ensure_resource_identity', unavailable)
    sync.run(full=True)
    assert db.learning_resources.count_documents({}) == 0
    monkeypatch.setattr(store, 'ensure_resource_identity', original)
    coordinator = AutoSyncCoordinator(
        pg_container.uow, environment_supported=True, source_health=source.adapter.health,
        interval_seconds=30, worker_stale_seconds=15, lease_seconds=30,
    )
    coordinator.heartbeat_and_claim('metadata-auto')
    coordinator.configure(True, 'resources')
    with pg_container.uow() as repo:
        repo.update('sync_auto_config', 'master-data', {'next_run_at': None})
    assert MasterSyncWorker(sync, coordinator).once('metadata-auto')['action'] == 'COMPLETED'
    assert db.learning_resources.count_documents({}) == 3
    assert coordinator.state()['auto_error'] is None


def test_legacy_objectid_shadow_is_not_converted_or_duplicated(mongo_test_database):
    uri, name = mongo_test_database
    with MongoClient(uri, serverSelectionTimeoutMS=3000, timeoutMS=10000) as client:
        database = client[name]
        key = '0123456789abcdef01234567'
        stamp = now()
        database.users.insert_one({'_id': ObjectId(key), 'created_at': stamp,
                                   'updated_at': stamp, 'bio': 'Legacy identity'})
        before = database.users.find_one({'_id': ObjectId(key)})
        store = MongoMetadataStore(database)
        document = UserProfileDocument(_id=key, created_at=stamp, updated_at=stamp)
        with pytest.raises(DomainError, match='BLOCKED_MONGO_ID_MAPPING'):
            store.ensure_user_identity(document)
        assert database.users.count_documents({}) == 1
        assert database.users.find_one({'_id': ObjectId(key)}) == before
        assert database.users.find_one({'_id': key}) is None


def assert_accounted(report):
    assert report['scanned'] == sum(report[key] for key in (
        'projected', 'unchanged', 'skipped_no_extension', 'skipped_unapproved', 'conflicts', 'failed',
    ))


def test_real_default_no_extension_counts_without_mongo_rpc(projection_runtime, monkeypatch):
    db, store, projection, sync = projection_runtime
    projection.identity_bootstrap = False

    def forbidden(*args):
        raise AssertionError('No Mongo RPC without extension source')

    monkeypatch.setattr(store, 'get_user', forbidden)
    monkeypatch.setattr(store, 'get_resource', forbidden)
    projection.identity_guard = forbidden
    sync.run(full=True)
    for report in projection.run().values():
        assert report['status'] == 'PARTIAL_SOURCE_FIELDS'
        assert report['scanned'] == report['skipped_no_extension'] > 0
        assert_accounted(report)
    assert db.users.count_documents({}) == db.learning_resources.count_documents({}) == 0


def test_real_one_approved_equal_source_id_unapproved_not_conflicts(source, projection_runtime):
    db, store, projection, sync = projection_runtime
    projection.identity_ids = {'resources': frozenset({'resource-1'})}
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE learning_resources SET mongo_id=id WHERE id='resource-1'"))
    sync.run(full=True)
    assert db.learning_resources.count_documents({}) == 1
    assert db.users.count_documents({}) == 0
    report = projection.run('resources')['resources']
    assert report['scanned'] == 3 and report['unchanged'] == 1
    assert report['skipped_unapproved'] == 2 and report['conflicts'] == 0
    assert report['status'] == 'READY_IDENTITY_ONLY'
    assert_accounted(report)
    assert set(db.learning_resources.find_one({'_id': 'resource-1'})) == {'_id', 'created_at', 'updated_at'}
    assert projection.run('users')['users']['status'] == 'NO_APPROVED_IDS'


def test_real_guard_failure_only_blocks_approved_ids_master_committed(pg_container, projection_runtime):
    db, store, projection, sync = projection_runtime
    projection.identity_ids = {'resources': frozenset({'resource-2'})}
    projection.batch_size = 3

    def unavailable(stream, ids):
        assert ids == ['resource-2']
        raise ConnectionError('Simulated reference guard downtime')

    original = projection.identity_guard
    projection.identity_guard = unavailable
    sync.run(full=True)
    report = projection.run('resources')['resources']
    assert report['conflicts'] == 1 and report['skipped_unapproved'] == 2
    assert report['reason'] == 'IDENTITY_GUARD_UNAVAILABLE'
    assert report['status'] == 'BLOCKED_MONGO_ID_MAPPING'
    assert_accounted(report)
    assert db.learning_resources.count_documents({}) == 0
    with pg_container.uow() as repo:
        assert repo.find('sync_runs', {'stream_name': 'resources'})[0]['status'] == 'COMPLETED'
    projection.identity_guard = original
    recovered = projection.run('resources')['resources']
    assert recovered['projected'] == 1 and recovered['skipped_unapproved'] == 2
    assert recovered['status'] == 'READY_IDENTITY_ONLY'
