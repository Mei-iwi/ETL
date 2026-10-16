"""Controlled identity/enrichment: real SQLite transactions, isolated Mongo port doubles."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure
from test_master_metadata_projection import MetadataMemory

from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
from data_sync_etl.application.master_metadata_projection import MasterMetadataProjection
from data_sync_etl.application.source_mongo_metadata_sync import SourceMongoMetadataSync
from data_sync_etl.cli.main import dispatch, parser
from data_sync_etl.composition import Container
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import ResourceDescriptionDocument, UserProfileDocument
from data_sync_etl.domain.source_ownership import state_hash


@pytest.fixture
def verified(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    return container


def projection(c, store=None, **kwargs):
    kwargs.setdefault('identity_scope', 'source_verified')
    kwargs.setdefault('identity_guard', lambda stream, ids: dict.fromkeys(ids))
    return MasterMetadataProjection(c.uow, store or MetadataMemory(), identity_bootstrap=True,
                                    batch_size=1, **kwargs)


def master(c):
    with c.uow() as repo:
        return {t: repo.find(t, order=('stream_name' if t == 'sync_checkpoints' else 'id',))
                for t in ('master_users', 'master_learning_resources', 'sync_checkpoints')}


def test_dry_run_then_apply_identity_coverage_idempotency_and_hash(verified):
    c, store = verified, MetadataMemory()
    p = projection(c, store)
    before = master(c)
    preview = dispatch(type('Runtime', (), {'settings': c.settings, 'master_metadata_projection': p})(),
                       parser().parse_args(['project-master-mongo', '--full']))
    assert master(c) == before and not store.calls
    for report in preview.values():
        assert report['status'] == 'DRY_RUN' and report['created'] == 0
        assert report['would_create'] == report['eligible'] == report['missing_document'] > 0
        assert report['synced_fields'] == []
    applied = p.run()
    for report in applied.values():
        assert report['status'] == 'READY_IDENTITY_ONLY'
        assert report['created'] == report['eligible'] == report['missing_extension'] > 0
        assert report['identity_coverage_percent'] == 100 and report['extension_coverage_percent'] == 0
        assert report['missing_document'] == 0 and report['last_run']
        assert report['synced_fields'] == ['_id', 'created_at', 'updated_at']
    old = {k: v.bson() for k, v in store.resources.items()}
    assert all(set(d) == {'_id', 'created_at', 'updated_at'} for d in old.values())
    assert all(r['created'] == 0 and r['unchanged'] == r['eligible'] for r in p.run().values())
    assert {k: v.bson() for k, v in store.resources.items()} == old
    for table, rows in master(c).items():
        if table != 'sync_checkpoints':
            assert all(r['source_state_hash'] == state_hash(r) for r in rows)
    assert c.sync.run()['resources']['updated'] == 0


def test_nonpk_and_existing_extensions_are_preserved(verified):
    c, store = verified, MetadataMemory()
    with c.uow() as repo:
        repo.sync_upsert('master_learning_resources', {'id': 'resource-1', 'mongo_id': 'NON-PK'}, source_origin='POSTGRESQL')
    stamp = now() + timedelta(days=1)
    doc = ResourceDescriptionDocument(_id='NON-PK', created_at=stamp, updated_at=stamp,
                                      description='Mô tả thật', tags=['toán'], authors=[{'name': 'Tác giả'}])
    user = UserProfileDocument(_id='user-1', created_at=stamp, updated_at=stamp, bio='Tiểu sử thật')
    store.resources[doc.id], store.users[user.id] = doc, user
    p = projection(c, store, identity_guard=lambda stream, ids: {
        key: 'NON-PK' if key == 'resource-1' else None for key in ids})
    report = p.run()
    assert report['resources']['extension_present'] == report['users']['extension_present'] == 1
    assert store.resources['NON-PK'].bson() == doc.bson() and store.users['user-1'].bson() == user.bson()
    assert 'resource-1' not in store.resources


@pytest.mark.parametrize('changes', [
    {'is_active': False}, {'deleted_at': now()}, {'publication_status': 'ARCHIVED'},
    {'source_origin': 'UNKNOWN'},
])
def test_ineligible_rows_are_not_read_or_written(verified, changes):
    c, store = verified, MetadataMemory()
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', changes)
    before = master(c)
    report = projection(c, store).run('resources')['resources']
    assert report['skipped_ineligible'] == 1 and 'resource-1' not in store.resources
    assert report['created'] == 2
    after = master(c)
    assert after['master_learning_resources'][0] == before['master_learning_resources'][0]


def test_local_requires_explicit_approval_and_existing_mapping(verified):
    c, store = verified, MetadataMemory()
    with c.uow() as repo:
        repo.update('master_users', 'user-1', {'source_origin': 'LOCAL'})
    p = projection(c, store, identity_scope='approved_ids', identity_ids={'users': ['user-1']})
    report = p.run('users')['users']
    assert report['missing_mapping'] == 1 and report['status'] == 'PARTIAL' and not store.users
    with c.uow() as repo:
        repo.update('master_users', 'user-1', {'mongo_id': 'LOCAL-MAP'})
    assert p.run('users')['users']['created'] == 1
    assert set(store.users) == {'LOCAL-MAP'}
    assert projection(c, store).run('users')['users']['skipped_not_source_backed'] == 1


@pytest.mark.parametrize('reference', ['WRONG-ID', ' bad ', 123])
def test_source_mismatch_never_reserves_or_creates(verified, reference):
    c, store = verified, MetadataMemory()
    before = master(c)
    report = projection(c, store, identity_guard=lambda stream, ids: dict.fromkeys(ids, reference)).run('resources')['resources']
    assert report['conflicts'] == 3 and not store.resources and master(c) == before


def test_unavailable_source_is_fail_closed_and_sanitized(verified, caplog):
    c, store = verified, MetadataMemory()
    def unavailable(*args):
        raise RuntimeError('SECRET_SOURCE_URI')
    before = master(c)
    report = projection(c, store, identity_guard=unavailable).run('users')['users']
    assert report['reason'] == 'IDENTITY_GUARD_UNAVAILABLE' and not store.users
    assert master(c) == before and 'SECRET_SOURCE_URI' not in caplog.text


def test_identity_outage_postcommit_report_and_retry(verified):
    c, store = verified, MetadataMemory()
    store.fail_on = 2
    c.sync.skip_shared_metadata_validation = False
    c.sync.metadata_projection = projection(c, store)
    result = c.sync.run('resources')
    assert result['resources']['failed'] == 0
    report = c.sync.last_metadata_report['resources']
    assert report['status'] == 'FAILED' and report['created'] == 1 and report['missing_document'] == 1
    with c.uow() as repo:
        assert repo.get('sync_runs', result['resources']['run_id'])['status'] == 'COMPLETED'
        assert all(r['source_state_hash'] == state_hash(r) for r in repo.find('master_learning_resources'))
    checkpoint = master(c)['sync_checkpoints']
    store.fail_on = None
    report = c.sync.metadata_projection.run('resources')['resources']
    assert report['created'] == 2 and report['unchanged'] == 1 and report['missing_document'] == 0
    assert master(c)['sync_checkpoints'] == checkpoint


def test_mapping_or_eligibility_changed_during_mongo_read_is_blocked(verified):
    c, store = verified, MetadataMemory()
    def race(key):
        if key == 'resource-1':
            with c.uow() as repo:
                repo.update('master_learning_resources', key, {'is_active': False})
        return None
    store.get_resource = race
    report = projection(c, store).run('resources')['resources']
    assert report['conflict_reasons'] == {'MONGO_METADATA_MAPPING_CHANGED': 1}
    assert 'resource-1' not in store.resources


def test_objectid_collision_and_legacy_validation_do_not_overwrite(verified):
    c = verified
    reference = 'a' * 24
    with c.uow() as repo:
        repo.sync_upsert('master_users', {'id': 'user-1', 'mongo_id': reference}, source_origin='POSTGRESQL')
    database = MagicMock()
    database.with_options.return_value = database
    def collision(query, fields):
        return {'_id': ObjectId(reference)} if isinstance(query['_id'], ObjectId) else None
    database['users'].find_one.side_effect = collision
    store = MongoMetadataStore(database)
    p = projection(c, store, identity_scope='approved_ids', identity_ids={'users': ['user-1']},
                   identity_guard=lambda stream, ids: dict.fromkeys(ids, reference))
    assert p.run('users')['users']['conflict_reasons'] == {'BLOCKED_MONGO_ID_MAPPING': 1}
    database['users'].update_one.assert_not_called()
    database['users'].find_one.side_effect = lambda query, fields: None if isinstance(query['_id'], ObjectId) else {
        '_id': reference, 'bio': 12, 'created_at': now(), 'updated_at': now()}
    assert p.run('users')['users']['invalid'] == 1
    database['users'].update_one.assert_not_called()


def test_separate_partial_update_never_unsets_and_newer_identity_is_stale(monkeypatch):
    store = MongoMetadataStore(MagicMock())
    collection = store.database['learning_resources']
    collection.name = 'learning_resources'
    stamp = now()
    incoming = ResourceDescriptionDocument(_id='R', created_at=stamp, updated_at=stamp, description='New')
    old = ResourceDescriptionDocument(_id='R', created_at=stamp, updated_at=stamp + timedelta(days=1))
    monkeypatch.setattr(store, '_get', lambda *args: old)
    collection.find_one.return_value = old.bson()
    assert store.sync_source_metadata('resources', incoming) == 'STALE'
    collection.update_one.assert_not_called()
    old = ResourceDescriptionDocument(_id='R', created_at=stamp, updated_at=stamp - timedelta(days=1),
                                      description='Old', tags=['keep'], authors=[{'name': 'Keep'}])
    collection.find_one.return_value = old.bson()
    collection.update_one.return_value.matched_count = 1
    assert store.sync_source_metadata('resources', incoming) == 'UPDATED'
    update = collection.update_one.call_args.args[1]
    assert set(update) == {'$set'} and set(update['$set']) == {'description', 'updated_at'}
    assert 'tags' not in update['$set'] and 'authors' not in update['$set']
    collection.update_one.side_effect = OperationFailure('PRIVATE', code=121)
    with pytest.raises(DomainError, match='MONGO_METADATA_DOCUMENT_INVALID'):
        store.sync_source_metadata('resources', incoming)


def test_separate_dry_run_and_shared_fallback_validation_never_write(verified):
    c, source, destination = verified, MagicMock(), MagicMock()
    source.assert_distinct_destination = MagicMock()
    with c.uow() as repo:
        repo.reserve_master_mongo_identity('users', 'user-1', None, 'user-1')
    source.get_user.return_value = UserProfileDocument(_id='user-1', created_at=now(), updated_at=now(), bio='Real')
    shared = SourceMongoMetadataSync(c.uow, source, None, shared=True, reference_guard=lambda stream, ids: dict.fromkeys(ids))
    assert shared.run('users')['users']['matched'] == 1
    source.sync_source_metadata.assert_not_called()
    def refs(stream, ids):
        return {key: 'user-1' if key == 'user-1' else None for key in ids}
    separate = SourceMongoMetadataSync(c.uow, source, destination, reference_guard=refs)
    report = separate.run('users', dry_run=True)['users']
    assert report['would_sync'] == 1, report
    source.assert_distinct_destination.assert_called_once()
    destination.sync_source_metadata.assert_not_called()


@pytest.mark.parametrize('environment,mode,adapter', [
    ('production', 'shared', 'postgresql'), (None, 'shared', 'postgresql'),
    ('development', 'separate', 'postgresql'), ('development', 'shared', 'json'),
])
def test_new_optin_rejects_unsafe_configuration_before_clients(container, monkeypatch, environment, mode, adapter):
    factory = MagicMock()
    monkeypatch.setattr('data_sync_etl.composition.create_source', factory)
    settings = container.settings.model_copy(update=dict(master_mongo_identity_projection=True,
        app_environment=environment, mongo_metadata_mode=mode, source_adapter=adapter))
    with pytest.raises(DomainError, match='MONGO_IDENTITY_PROJECTION_REQUIRES_SHARED_DEV_POSTGRES'):
        Container(settings, container.engine)
    factory.assert_not_called()


def test_cli_apply_rejects_unknown_environment_before_any_run(verified):
    runtime = MagicMock()
    runtime.settings.app_environment = None
    with pytest.raises(DomainError, match='MONGO_METADATA_APPLY_REQUIRES_DEV'):
        dispatch(runtime, parser().parse_args(['project-master-mongo', '--full', '--apply']))
    runtime.master_metadata_projection.run.assert_not_called()


@pytest.mark.parametrize('code,expected', [(121, 'invalid'), (13, 'failed')])
def test_identity_insert_validation_vs_permission_failure(verified, monkeypatch, code, expected):
    database = MagicMock()
    database.with_options.return_value = database
    database['users'].find_one.return_value = None
    database['users'].update_one.side_effect = OperationFailure('PRIVATE', code=code)
    store = MongoMetadataStore(database)
    monkeypatch.setattr(store, 'get_user', lambda _: None)
    p = projection(verified, store, identity_scope='approved_ids', identity_ids={'users': ['user-1']})
    report = p.run('users')['users']
    assert report[expected] == 1 and report['missing_document'] == 1 and report['created'] == 0


def test_shared_optin_hooks_existing_projector_after_commit(container, monkeypatch):
    store = MetadataMemory()
    source = MagicMock(spec=PostgreSQLSourceAdapter)
    source.source_origin = 'POSTGRESQL'
    source.batches.side_effect = container.source.batches
    source.metadata_identity_references.side_effect = lambda stream, ids: dict.fromkeys(ids)
    monkeypatch.setattr('data_sync_etl.composition.create_source', lambda _: source)
    monkeypatch.setattr('data_sync_etl.composition.MongoClient', MagicMock())
    monkeypatch.setattr('data_sync_etl.composition.MongoMetadataStore', lambda _: store)
    settings = container.settings.model_copy(update=dict(master_mongo_identity_projection=True,
        app_environment='test', source_adapter='postgresql', master_mongo_identity_scope='source_verified'))
    runtime = Container(settings, container.engine)
    try:
        assert isinstance(runtime.master_metadata_projection, MasterMetadataProjection)
        assert runtime.sync.metadata_projection is runtime.master_metadata_projection
        assert runtime.mongo_metadata_sync.shared and runtime.mongo_metadata_sync.destination is None
        assert not runtime.settings.source_mongo_enabled and not runtime.settings.master_mongo_identity_bootstrap
        assert store.calls == 0
        result = runtime.sync.run(full=True)
        assert result['resources']['inserted'] == 3 and result['users']['inserted'] == 3
        assert runtime.sync.last_metadata_report['resources']['created'] == 3
        assert runtime.sync.last_metadata_report['users']['created'] == 3
        assert len(store.users) == 3 and len(store.resources) == 3
        assert runtime.sync.run()['resources']['updated'] == 0
        assert runtime.sync.last_metadata_report['resources']['created'] == 0
    finally:
        runtime.close()
