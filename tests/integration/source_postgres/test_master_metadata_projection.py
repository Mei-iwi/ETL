import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from bson import ObjectId
from pymongo import MongoClient
from sqlalchemy import inspect, text

from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.application.auto_sync import AutoSyncCoordinator
from data_sync_etl.application.master_metadata_projection import MasterMetadataProjection
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.db.models import MODELS
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import UserProfileDocument
from data_sync_etl.workers.master_sync import MasterSyncWorker
from scripts.source_seed import seed_demo

pytestmark = pytest.mark.postgres


def test_qa26_full_field_mapping_and_verified_mongo_ids(source_database, pg_container, mongo_test_database):
    root = pg_container.source.root
    fixtures = {path: json.loads(path.read_text(encoding='utf-8')) for path in root.glob('*.json')}
    identifiers = {row['id']: 'QA26_' + row['id'].upper().replace('-', '_')
                   for rows in fixtures.values() for row in rows if 'id' in row}
    for path, rows in fixtures.items():
        for row in rows:
            for key, value in list(row.items()):
                if (key == 'id' or key.endswith('_id')) and value in identifiers:
                    row[key] = identifiers[value]
        if path.stem in ('users', 'resources'):
            rows[0]['mongo_id'] = 'MDOC-QA26-' + path.stem.upper()
        if path.stem == 'grades':
            rows[0]['mongo_id'] = 'a' * 24
        if path.stem == 'subjects':
            rows[0]['mongo_id'] = 'b' * 24  # No target Mongo subjects collection.
        path.write_text(json.dumps(rows), encoding='utf-8')
    with source_database.engine.begin() as connection:
        seed_demo(connection, root)
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000, timeoutMS=10000, tz_aware=True)
    try:
        db = client[mongo_test_database[1]]
        initialize(db, apply=True, dimension=pg_container.settings.vector_dimension)
        projection = MasterMetadataProjection(pg_container.uow, MongoMetadataStore(db),
            identity_bootstrap=True, identity_scope='source_verified', batch_size=1,
            identity_guard=source_database.adapter.metadata_identity_references)
        sync = MasterSync(source_database.adapter, pg_container.uow, batch_size=1)
        result = sync.run(full=True)
        assert all(r['failed'] == 0 for r in result.values())
        for stream, table in [('users', 'master_users'), ('resources', 'master_learning_resources'),
                              ('grades', 'master_grade_levels'), ('subjects', 'subjects')]:
            expected = [a for batch in source_database.adapter.batches(stream, None, 1) for a in batch]
            with pg_container.uow() as repo:
                assert repo.count(table, {}) == len(expected)
                for aggregate in expected:
                    row = repo.get(table, aggregate.values['id'])
                    assert all(row[k] == value for k, value in aggregate.values.items()
                               if k not in ('email', 'hashed_password'))
                    assert row['id'].startswith('QA26_')
        report = projection.run()
        assert all(r['status'] == 'READY_IDENTITY_ONLY' and r['failed'] == 0 for r in report.values())
        for collection in ('users', 'learning_resources'):
            assert all(set(doc) == {'_id', 'created_at', 'updated_at'} for doc in db[collection].find({}))
        assert db.users.find_one({'_id': 'MDOC-QA26-USERS'}) is not None
        assert db.learning_resources.find_one({'_id': 'MDOC-QA26-RESOURCES'}) is not None
        assert all(r['projected'] == 0 for r in projection.run().values())
        assert not {'grades', 'subjects', 'resource_versions'}.intersection(db.list_collection_names())
    finally:
        client.close()


def test_real_master_ten_table_columns_match_orm_and_reference_constraints(pg_container):
    inspector = inspect(pg_container.engine)
    # Revision 0015 added source_origin/source_state_hash/source_missing_at to
    # the four SOURCE-backed Master tables; the business columns are unchanged.
    counts = {'master_learning_resources': 29, 'master_users': 23, 'master_grade_levels': 24,
              'subjects': 12, 'resource_subjects': 3, 'resource_grade_levels': 2,
              'resource_versions': 20, 'content_units': 13, 'ocr_jobs': 12, 'ocr_page_tasks': 14}
    for table, count in counts.items():
        actual = {c['name']: c for c in inspector.get_columns(table)}
        expected = MODELS[table].__table__.columns
        assert len(actual) == len(expected) == count
        assert set(actual) == set(expected.keys())
        for column in expected:
            assert actual[column.name]['nullable'] == column.nullable
            assert str(actual[column.name]['type'].compile(dialect=pg_container.engine.dialect)) == str(
                column.type.compile(dialect=pg_container.engine.dialect))
    for table in ('master_users', 'master_learning_resources'):
        assert any(i['unique'] and i['column_names'] == ['mongo_id'] for i in inspector.get_indexes(table))
        assert not any('mongo_id' in f['constrained_columns'] for f in inspector.get_foreign_keys(table))
        assert next(c for c in inspector.get_columns(table) if c['name'] == 'mongo_id')['default'] is None


def test_real_incremental_null_to_declared_reference_and_grade_mapping(source, pg_container):
    sync = MasterSync(source.adapter, pg_container.uow, batch_size=1)
    sync.run(full=True)
    with source.engine.connect() as connection:
        before = connection.scalar(text("SELECT updated_at FROM users WHERE id='user-1'"))
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE users SET mongo_id='MDOC-QA26-USER' WHERE id='user-1'"))
        connection.execute(text("UPDATE grade_levels SET mongo_id=:ref WHERE id='grade-1'"), {'ref': 'a' * 24})
    result = sync.run('users', full=False)
    assert result['users']['updated'] == 1
    sync.run('grades', full=False)
    with source.engine.connect() as connection:
        assert connection.scalar(text("SELECT updated_at FROM users WHERE id='user-1'")) > before
    with pg_container.uow() as repo:
        assert repo.get('master_users', 'user-1')['mongo_id'] == 'MDOC-QA26-USER'
        assert repo.get('master_grade_levels', 'grade-1')['grade_mongo_id'] == 'a' * 24
    repeated = sync.run('users', full=False)
    assert repeated['users']['updated'] == 0


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
            identity_guard=source.adapter.metadata_identity_references,
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


def test_real_identity_dry_run_then_apply_keeps_five_collections(pg_container, projection_runtime):
    db, _, projection, sync = projection_runtime
    sync.metadata_projection = None
    sync.run(full=True)
    with pg_container.uow() as repo:
        before = repo.find('master_learning_resources', order=('id',))
    preview = projection.run(dry_run=True)
    assert preview['resources']['would_create'] == 3 and preview['users']['would_create'] > 0
    assert db.users.count_documents({}) == db.learning_resources.count_documents({}) == 0
    with pg_container.uow() as repo:
        assert repo.find('master_learning_resources', order=('id',)) == before
    report = projection.run()
    assert report['resources']['created'] == 3 and report['resources']['identity_coverage_percent'] == 100
    assert report['resources']['missing_extension'] == 3
    assert set(db.list_collection_names()) == {
        'users', 'learning_resources', 'ocr_pages', 'mongo_content_units', 'content_bags'}
    dates = {d['_id']: d['created_at'] for d in db.learning_resources.find({})}
    assert projection.run()['resources']['unchanged'] == 3
    assert dates == {d['_id']: d['created_at'] for d in db.learning_resources.find({})}


def test_real_declared_nonpk_reference_preserves_existing_document(source, projection_runtime):
    db, store, projection, sync = projection_runtime
    with source.engine.begin() as conn:
        conn.execute(text("UPDATE learning_resources SET mongo_id='old-external-id' WHERE id='resource-1'"))
    stamp = now()
    db.learning_resources.insert_one({'_id': 'old-external-id', 'created_at': stamp, 'updated_at': stamp, 'tags': ['old']})
    before = db.learning_resources.find_one({'_id': 'old-external-id'})
    sync.run(full=True)
    report = projection.run('resources')['resources']
    assert report['status'] == 'READY_IDENTITY_ONLY' and report['conflicts'] == 0
    assert report['unchanged'] == 3
    assert db.learning_resources.find_one({'_id': 'resource-1'}) is None
    assert db.learning_resources.find_one({'_id': 'old-external-id'}) == before


def test_real_mongo_rpc_runs_without_open_master_transaction(pg_container, projection_runtime, monkeypatch):
    _, store, _, sync = projection_runtime
    calls = []

    def checked(method):
        def invoke(*args):
            with pg_container.engine.connect().execution_options(isolation_level='AUTOCOMMIT') as connection:
                active = connection.execute(text(
                    'SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() '
                    'AND pid<>pg_backend_pid() AND xact_start IS NOT NULL'
                )).scalar_one()
            assert active == 0
            calls.append(method.__name__)
            return method(*args)
        return invoke

    for name in ('get_user', 'get_resource', 'ensure_user_identity', 'ensure_resource_identity'):
        monkeypatch.setattr(store, name, checked(getattr(store, name)))
    sync.run(full=True)
    assert all(name in calls for name in ('get_user', 'get_resource', 'ensure_user_identity', 'ensure_resource_identity'))


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
        'projected', 'unchanged', 'skipped_no_extension', 'skipped_unapproved', 'skipped_not_source_backed', 'conflicts', 'failed',
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


def test_source_verified_declared_nonpk_and_fallback_persist_without_content_changes(source, pg_container, projection_runtime):
    from data_sync_etl.cli.main import dispatch, parser

    db, store, projection, sync = projection_runtime
    with source.engine.begin() as c:
        c.execute(text("UPDATE users SET mongo_id='MDOC-U123' WHERE id='user-1'"))
        c.execute(text("UPDATE learning_resources SET mongo_id='MDOC-R123' WHERE id='resource-1'"))
    stamp = now()
    db.users.insert_one({'_id': 'MDOC-U123', 'created_at': stamp, 'updated_at': stamp, 'bio': 'Owner managed'})
    preserved_user = db.users.find_one({'_id': 'MDOC-U123'})
    db.ocr_pages.insert_one({'_id': 'job-keep:1', 'created_at': stamp, 'updated_at': stamp,
                            'job_id': 'job-keep', 'resource_version_id': 'ver-keep', 'page_num': 1,
                            'raw_text': 'keep', 'char_count': 4, 'source_checksum': 'keep'})
    db.mongo_content_units.insert_one({'_id': 'unit-keep', 'created_at': stamp, 'updated_at': stamp,
                                      'resource_version_id': 'ver-keep', 'text': 'keep', 'markdown': 'keep'})
    db.content_bags.insert_one({'_id': 'bag-keep', 'created_at': stamp, 'updated_at': stamp,
                               'resource_version_id': 'ver-keep', 'unit_type': 'SECTION', 'keywords': ['keep'],
                               'embedding_text': 'keep', 'embedding': [1.0] + [0.0] * 255,
                               'embedding_model': 'test-fixture-only'})
    content = {name: list(db[name].find({})) for name in ('ocr_pages', 'mongo_content_units', 'content_bags')}
    projection.identity_scope = 'source_verified'
    projection.identity_ids = {}
    pg_container.sync = sync
    pg_container.master_metadata_projection = projection
    result = dispatch(pg_container, parser().parse_args(['sync-master', '--full']))
    assert result['resources']['inserted'] == 3
    with pg_container.uow() as repo:
        assert repo.get('master_users', 'user-1')['mongo_id'] == 'MDOC-U123'
        assert repo.get('master_learning_resources', 'resource-1')['mongo_id'] == 'MDOC-R123'
        assert repo.get('master_learning_resources', 'resource-2')['mongo_id'] == 'resource-2'
        # Master-only row must not be implicitly admitted in source_verified.
        template = repo.get('master_learning_resources', 'resource-1')
        repo.insert('master_learning_resources', {**template, 'id': 'API-ONLY', 'mongo_id': None})
    again = dispatch(pg_container, parser().parse_args(['project-master-mongo', '--full']))
    assert again['resources']['projected'] == 0 and again['resources']['unchanged'] == 3
    assert again['resources']['skipped_not_source_backed'] == 1
    assert db.users.find_one({'_id': 'MDOC-U123'}) == preserved_user
    assert db.learning_resources.find_one({'_id': 'MDOC-R123'}) is not None
    assert db.learning_resources.find_one({'_id': 'resource-1'}) is None
    # NULL upstream leaves local fallback untouched; ordinary parent updates still sync.
    with source.engine.begin() as c:
        c.execute(text("UPDATE providers SET name='Updated provider' WHERE id='provider-2'"))
    sync.run('resources')
    with pg_container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-2')['mongo_id'] == 'resource-2'
    assert content == {name: list(db[name].find({})) for name in content}


def test_source_mapping_remap_is_blocked_without_moving_mongo(source, pg_container, projection_runtime):
    db, store, projection, sync = projection_runtime
    with source.engine.begin() as c:
        c.execute(text("UPDATE learning_resources SET mongo_id='MDOC-A' WHERE id='resource-1'"))
    sync.run(full=True)
    before = db.learning_resources.find_one({'_id': 'MDOC-A'})
    with source.engine.begin() as c:
        c.execute(text("UPDATE learning_resources SET mongo_id='MDOC-B' WHERE id='resource-1'"))
    with pytest.raises(DomainError, match='BLOCKED_MONGO_ID_MAPPING'):
        sync.run('resources')
    with pg_container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['mongo_id'] == 'MDOC-A'
        last = repo.find('sync_runs', {'stream_name': 'resources'}, order=('-started_at',), limit=1)[0]
        assert last['error_message'] == 'BLOCKED_MONGO_ID_MAPPING'
    report = projection.run('resources')['resources']
    assert report['conflicts'] == 1 and report['status'] == 'BLOCKED_MONGO_ID_MAPPING'
    assert db.learning_resources.find_one({'_id': 'MDOC-A'}) == before
    assert db.learning_resources.find_one({'_id': 'MDOC-B'}) is None
