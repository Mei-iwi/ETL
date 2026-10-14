from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
from data_sync_etl.application.auto_sync import AutoSyncCoordinator
from data_sync_etl.application.master_metadata_projection import MasterMetadataProjection
from data_sync_etl.cli.main import dispatch, parser
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import ResourceDescriptionDocument, UserProfileDocument
from data_sync_etl.main import create_app
from data_sync_etl.workers.master_sync import MasterSyncWorker


class MetadataMemory:
    """Flow double only; actual Mongo atomicity is covered in integration tests."""

    def __init__(self):
        self.users, self.resources = {}, {}
        self.calls = 0
        self.fail_on = None

    def get_user(self, key):
        return self.users.get(key)

    def get_resource(self, key):
        return self.resources.get(key)

    def ensure_user_identity(self, document):
        return self._ensure(self.users, document)

    def ensure_resource_identity(self, document):
        return self._ensure(self.resources, document)

    def _ensure(self, target, document):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError('private URI/password must not be logged')
        created = document.id not in target
        target.setdefault(document.id, document)
        return created


def approved(container):
    with container.uow() as repo:
        return {stream: [r['id'] for r in repo.master_metadata_page(stream, None, 1000)]
                for stream in ('users', 'resources')}


def projector(container, store, **kwargs):
    return MasterMetadataProjection(container.uow, store, batch_size=1,
                                    identity_bootstrap=True, identity_ids=approved(container), **kwargs)


def test_default_no_extension_no_fake_fields_or_mongo_rpc(container):
    container.sync.run(full=True)
    store = MagicMock()
    result = MasterMetadataProjection(container.uow, store, batch_size=1).run()
    assert not store.mock_calls
    for report in result.values():
        assert report['scanned'] == report['skipped_no_extension'] > 0
        assert report['status'] == 'PARTIAL_SOURCE_FIELDS'
        assert report['projected'] == report['failed'] == report['conflicts'] == 0


def test_bootstrap_identity_only_idempotent_and_preserves_extensions(container):
    container.sync.run(full=True)
    store = MetadataMemory()
    stamp = now() - timedelta(days=1)
    store.users['user-1'] = UserProfileDocument(_id='user-1', created_at=stamp, updated_at=stamp, bio='Managed by Mongo')
    store.resources['resource-1'] = ResourceDescriptionDocument(
        _id='resource-1', created_at=stamp, updated_at=stamp,
        description='Independent', authors=[{'name': 'Author'}], tags=['Toán'],
    )
    old_user, old_resource = store.users['user-1'].bson(), store.resources['resource-1'].bson()
    usecase = projector(container, store)
    first = usecase.run()
    assert first['users']['unchanged'] == first['resources']['unchanged'] == 1
    assert store.users['user-1'].bson() == old_user
    assert store.resources['resource-1'].bson() == old_resource
    for target in (store.users, store.resources):
        for key, doc in target.items():
            if key not in ('user-1', 'resource-1'):
                assert set(doc.bson()) == {'_id', 'created_at', 'updated_at'}
    second = usecase.run()
    assert all(r['projected'] == 0 and r['unchanged'] == r['scanned'] for r in second.values())


def test_bootstrap_unapproved_ids_are_skipped_not_conflicts(container):
    container.sync.run(full=True)
    store = MetadataMemory()
    projection = MasterMetadataProjection(container.uow, store, identity_bootstrap=True,
                                          identity_ids={'users': ['user-1']})
    report = projection.run('users')['users']
    assert report['skipped_unapproved'] == report['scanned'] - 1
    assert report['conflicts'] == 0
    assert report['projected'] == 1
    assert report['status'] == 'READY_IDENTITY_ONLY'
    assert set(store.users) == {'user-1'}


def assert_accounted(report):
    assert report['scanned'] == sum(report[key] for key in (
        'projected', 'unchanged', 'skipped_no_extension', 'skipped_unapproved', 'skipped_not_source_backed', 'conflicts', 'failed',
    ))


def test_empty_whitelist_no_fake_ready_no_guard_or_mongo(container):
    container.sync.run(full=True)
    store, guard = MagicMock(), MagicMock()
    result = MasterMetadataProjection(container.uow, store, identity_bootstrap=True,
                                      identity_guard=guard).run()
    for report in result.values():
        assert report['status'] == report['reason'] == 'NO_APPROVED_IDS'
        assert report['scanned'] == report['skipped_unapproved'] > 0
        assert report['conflicts'] == report['projected'] == report['failed'] == 0
        assert_accounted(report)
    assert not store.mock_calls and not guard.mock_calls


def test_guard_unavailable_counts_only_approved_records(container, caplog):
    container.sync.run(full=True)
    store = MagicMock()

    def unavailable(stream, ids):
        assert ids == ['resource-2']
        raise RuntimeError('DO_NOT_LOG_PASSWORD_TOKEN_STORAGE_KEY')

    projection = MasterMetadataProjection(container.uow, store, batch_size=3,
                                          identity_bootstrap=True, identity_ids={'resources': ['resource-2']},
                                          identity_guard=unavailable)
    with caplog.at_level('INFO'):
        report = projection.run('resources')['resources']
    assert report['status'] == 'BLOCKED_MONGO_ID_MAPPING'
    assert report['reason'] == 'IDENTITY_GUARD_UNAVAILABLE'
    assert report['conflicts'] == 1 and report['skipped_unapproved'] == 2
    assert report['scanned'] == 3
    assert_accounted(report)
    assert not store.mock_calls
    assert 'DO_NOT_LOG_PASSWORD_TOKEN_STORAGE_KEY' not in caplog.text


def test_unapproved_batches_never_call_guard(container):
    container.sync.run(full=True)
    store, guard = MagicMock(), MagicMock()
    guard.side_effect = RuntimeError('Must not call guard with empty requested IDs')
    projection = MasterMetadataProjection(container.uow, store, batch_size=1,
                                          identity_bootstrap=True, identity_ids={'users': ['not-in-master']},
                                          identity_guard=guard)
    report = projection.run('users')['users']
    assert report['status'] == 'NO_APPROVED_IDS'
    assert report['scanned'] == report['skipped_unapproved']
    assert_accounted(report)
    assert not store.mock_calls and not guard.mock_calls


def test_default_counts_without_fetching_master_records(container, monkeypatch):
    container.sync.run(full=True)

    def forbidden(*args, **kwargs):
        raise AssertionError('Missing-extension check must not fetch Master payloads')

    monkeypatch.setattr(SQLAlchemyRepository, 'master_metadata_page', forbidden)
    store, guard = MagicMock(), MagicMock()
    result = MasterMetadataProjection(container.uow, store, identity_guard=guard).run()
    for report in result.values():
        assert report['scanned'] == report['skipped_no_extension'] > 0
        assert report['reason'] == 'SKIPPED_NO_EXTENSION'
        assert_accounted(report)
    assert not store.mock_calls and not guard.mock_calls


def test_conflicting_reference_skips_only_unsafe_record(container):
    container.sync.run(full=True)
    store = MetadataMemory()
    projection = projector(container, store, identity_guard=lambda stream, ids: {
        key: 'different-from-committed-master' if key == 'resource-1' else None for key in ids
    })
    result = projection.run('resources')['resources']
    assert result['conflicts'] == 1 and result['projected'] == result['scanned'] - 1
    assert 'resource-1' not in store.resources


def test_mapping_audit_failure_cannot_create_documents(container):
    container.sync.run(full=True)
    store = MetadataMemory()

    def unavailable(*args):
        raise RuntimeError('private source URL')

    report = projector(container, store, identity_guard=unavailable).run('resources')['resources']
    assert report['status'] == 'BLOCKED_MONGO_ID_MAPPING'
    assert not store.resources and store.calls == 0


def test_soft_delete_inactive_no_delete_or_visibility_change(container):
    container.sync.run(full=True)
    store = MetadataMemory()
    projection = projector(container, store)
    projection.run()
    resource_before = store.resources['resource-1'].bson()
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'is_active': False, 'deleted_at': now()})
        repo.update('master_users', 'user-1', {'is_active': False, 'deleted_at': now()})
    projection.run()
    assert store.resources['resource-1'].bson() == resource_before
    with container.uow() as repo:
        assert not repo.get('master_learning_resources', 'resource-1')['is_active']


def test_keyset_multiple_pages_without_auth_or_source_reader(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        template = repo.get('master_users', 'user-1')
        for key in ['A', 'Z', 'a', 'z', *[f'extra-{n:03d}' for n in range(17)]]:
            repo.insert('master_users', {**template, 'id': key, 'hashed_password': 'private-test-only'})
        expected = repo.count('master_users', {})
        assert set(repo.master_metadata_page('users', None, 1)[0]) == {'id', 'mongo_id', 'created_at', 'updated_at'}
    store = MetadataMemory()
    projection = projector(container, store)
    # Projection must still work if the source reader is entirely unavailable.
    container.source = None
    result = projection.run('users')['users']
    assert result['scanned'] == result['projected'] == len(store.users) == expected


def test_post_commit_failure_keeps_master_counters_checkpoints_and_retry_recovers(container, caplog):
    store = MetadataMemory()
    store.fail_on = 2
    projection = MasterMetadataProjection(container.uow, store, batch_size=1, identity_bootstrap=True,
                                          identity_ids={'resources': ['resource-1', 'resource-2', 'resource-3']})
    container.sync.metadata_projection = projection
    with caplog.at_level('INFO'):
        result = container.sync.run(full=True)
    assert result['resources']['inserted'] == 3
    assert 'private URI/password' not in caplog.text
    with container.uow() as repo:
        assert repo.count('master_learning_resources', {}) == 3
        assert repo.find('sync_runs', {'stream_name': 'resources'})[0]['status'] == 'COMPLETED'
        checkpoint = repo.get('sync_checkpoints', 'resources')
    store.fail_on = None
    reconciled = projection.run('resources')['resources']
    assert reconciled['projected'] == 2 and reconciled['unchanged'] == 1
    with container.uow() as repo:
        assert repo.get('sync_checkpoints', 'resources') == checkpoint


def test_projection_hook_after_commit_for_manual_auto_and_no_change(container):
    calls = []

    class Observer:
        def run(self, stream):
            with container.uow() as repo:
                last = repo.find('sync_runs', {'stream_name': stream}, order=('-started_at',), limit=1)[0]
                assert last['status'] == 'COMPLETED'
                assert repo.master_metadata_page(stream, None, 1)
            calls.append(stream)

    container.sync.metadata_projection = Observer()
    result = dispatch(container, parser().parse_args(['sync-master', '--full']))
    assert calls == ['users', 'resources']
    assert all(set(r) == {'run_id', 'records_read', 'inserted', 'updated', 'skipped', 'failed'}
               for r in result.values())
    container.sync.run('resources', full=True)
    assert calls[-1] == 'resources'  # Reconcile even if every row is SKIPPED.
    coordinator = AutoSyncCoordinator(container.uow, environment_supported=True, source_health=lambda: None,
                                      interval_seconds=30, worker_stale_seconds=15, lease_seconds=30)
    coordinator.heartbeat_and_claim('auto-test')
    coordinator.configure(True, 'resources')
    with container.uow() as repo:
        repo.update('sync_auto_config', 'master-data', {'next_run_at': None})
    assert MasterSyncWorker(container.sync, coordinator).once('auto-test')['action'] == 'COMPLETED'
    assert calls == ['users', 'resources', 'resources', 'resources']
    container.sync.run('grades')
    container.sync.run('subjects')
    assert len(calls) == 4


def test_metadata_outage_does_not_make_sync_http_fail(container):
    class OfflineProjection:
        def run(self, stream):
            raise RuntimeError('Mongo unavailable')

    container.sync.metadata_projection = OfflineProjection()
    container.settings.admin_enabled = True
    with TestClient(create_app(container), client=('127.0.0.1', 50000)) as api:
        response = api.post('/api/v1/master-data/sync', json={'stream': 'all', 'full': True})
    assert response.status_code == 200, response.text
    with container.uow() as repo:
        assert all(r['status'] == 'COMPLETED' for r in repo.find('sync_runs'))


def test_projection_lock_and_validation(container):
    container.sync.run(full=True)
    projection = projector(container, MetadataMemory())
    with container.uow.processing_lock('master-mongo:users'):
        assert projection.run('users')['users']['status'] == 'BUSY'
    with pytest.raises(DomainError):
        projection.run('grades')
    with pytest.raises(ValueError):
        MasterMetadataProjection(container.uow, None, batch_size=1001)
    with container.uow() as repo:
        with pytest.raises(ValueError):
            repo.master_metadata_page('grades', None, 1)


def test_safe_adapter_insert_only_validates_identity_without_unset():
    db = MagicMock()
    db.with_options.return_value = db
    db.__getitem__.return_value.update_one.return_value.upserted_id = 'user-1'
    store = MongoMetadataStore(db)
    document = UserProfileDocument(_id='user-1', created_at=now(), updated_at=now())
    assert store.ensure_user_identity(document)
    selector, update = db.__getitem__.return_value.update_one.call_args.args
    assert selector == {'_id': 'user-1'} and set(update) == {'$setOnInsert'}
    assert set(update['$setOnInsert']) == {'_id', 'created_at', 'updated_at'}
    with pytest.raises(DomainError):
        store.ensure_user_identity(document.model_copy(update={'bio': 'not identity'}))
    with pytest.raises(DomainError):
        store.ensure_user_identity({'_id': 'user-1', 'created_at': now(), 'updated_at': now(), 'email': 'private'})


def test_cli_no_approved_ids_is_informational_full_still_required(container, monkeypatch, capsys):
    import data_sync_etl.cli.main as cli

    container.sync.run(full=True)
    projection = MasterMetadataProjection(container.uow, MagicMock(), identity_bootstrap=True)
    runtime = SimpleNamespace(settings=container.settings, master_metadata_projection=projection, close=lambda: None)
    monkeypatch.setattr(cli, 'Container', lambda: runtime)
    monkeypatch.setattr(cli.sys, 'argv', ['etl', 'project-master-mongo', '--stream', 'users', '--full'])
    assert cli.main() == 0
    assert 'NO_APPROVED_IDS' in capsys.readouterr().out
    with pytest.raises(SystemExit) as error:
        parser().parse_args(['project-master-mongo'])
    assert error.value.code == 2
