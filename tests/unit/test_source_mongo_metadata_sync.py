from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from pymongo.errors import OperationFailure

from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
from data_sync_etl.adapters.source.mongo_metadata import MongoMetadataSourceReader
from data_sync_etl.application.source_mongo_metadata_sync import SourceMongoMetadataSync
from data_sync_etl.cli.main import parser
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import UserProfileDocument
from data_sync_etl.ports.mongo_foundation import MetadataDocumentPort, SourceMetadataPort


def test_defaults_disabled_and_cli_full_only():
    settings = Settings(_env_file=None)
    assert not settings.source_mongo_enabled and settings.source_mongo_database == 'source_metadata'
    assert settings.mongo_metadata_mode == 'shared'
    assert parser().parse_args(['sync-mongo-metadata', '--full']).stream == 'all'
    with pytest.raises(SystemExit):
        parser().parse_args(['sync-mongo-metadata', '--incremental'])


def test_same_database_endpoint_aliases_fail_closed():
    source, target = MagicMock(), MagicMock()
    source.name = target.name = 'same_db'
    source.client.admin.command.return_value = {'topologyVersion': {'processId': 'same-server'}}
    target.client.admin.command.return_value = {'topologyVersion': {'processId': 'same-server'}}
    reader = MongoMetadataSourceReader(source, target)
    with pytest.raises(DomainError, match='SOURCE_MONGO_SAME_DESTINATION'):
        reader.assert_distinct_destination()
    source.client.admin.command.return_value = {'topologyVersion': {'processId': 'server-1'}, 'hosts': ['node:27017']}
    target.client.admin.command.return_value = {'topologyVersion': {'processId': 'server-2'}, 'hosts': ['node:27017']}
    with pytest.raises(DomainError):
        reader.assert_distinct_destination()
    source.name = 'source_db'
    reader.assert_distinct_destination()
    source.create_collection.assert_not_called()


def configured(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.sync_upsert('master_users', {'id': 'user-1', 'mongo_id': 'MUSER'}, source_origin='POSTGRESQL')
        assert repo.source_metadata_page('users', None, 1)
    source, destination = MagicMock(spec=SourceMetadataPort), MagicMock(spec=MetadataDocumentPort)
    stamp = now() - timedelta(days=1)
    source.get_user.return_value = UserProfileDocument(_id='MUSER', bio='Source-owned bio',
        created_at=stamp, updated_at=stamp, subject_id='subject-1')
    destination.sync_source_metadata.return_value = 'INSERTED'
    service = SourceMongoMetadataSync(container.uow, source, destination, batch_size=1,
        reference_guard=lambda stream, ids: {key: 'MUSER' if key == 'user-1' else None for key in ids},
        subject_guard=lambda _: True)
    return service, source, destination


def test_null_source_never_falls_back_and_payload_has_no_pg_auth(container):
    service, source, destination = configured(container)
    report = service.run('users')['users']
    assert report['scanned'] > 0, (report['status'], report['error_code'])
    assert report['inserted'] == 1 and report['skipped_unmapped'] == report['scanned'] - 1, report
    document = destination.sync_source_metadata.call_args.args[1]
    assert document.id == 'MUSER' and 'email' not in document.bson() and 'hashed_password' not in document.bson()
    assert report['mode'] == 'FULL_RECONCILE' and not report['incremental_available']
    assert not report['checkpoint_persisted']


def test_wrong_subject_mapping_and_source_unavailable_fail_closed(container, caplog):
    service, source, destination = configured(container)
    service.subject_guard = lambda _: False
    report = service.run('users')['users']
    assert report['conflicts'] == 1 and report['conflict_reasons']['MONGO_METADATA_SUBJECT_MISMATCH'] == 1, report
    destination.sync_source_metadata.assert_not_called()
    source.get_user.side_effect = ConnectionError('DO_NOT_LOG_SECRET_OR_URI')
    report = service.run('users')['users']
    assert report['status'] == 'FAILED' and report['error_code'] == 'SOURCE_MONGO_READ_FAILED'
    assert 'DO_NOT_LOG_SECRET_OR_URI' not in caplog.text


def test_missing_extensions_inactive_and_disabled_are_not_success(container):
    service, source, destination = configured(container)
    source.get_user.return_value = UserProfileDocument(_id='MUSER', created_at=now(), updated_at=now())
    assert service.run('users')['users']['missing_extension'] == 1
    with container.uow() as repo:
        repo.update('master_users', 'user-1', {'is_active': False})
    assert service.run('users')['users']['skipped_ineligible'] == 1
    destination.sync_source_metadata.assert_not_called()
    assert SourceMongoMetadataSync(container.uow, None, None).run('users')['users']['status'] == 'DISABLED'


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('code', [121, 13])
def test_server_validation_is_document_conflict_but_permissions_are_failure(container, monkeypatch, existing, code, caplog):
    service, source, _ = configured(container)
    store = MongoMetadataStore(MagicMock())
    collection = store.database['users']
    collection.name = 'users'
    stamp = now() - timedelta(days=2)
    old = UserProfileDocument(_id='MUSER', bio='old', created_at=stamp, updated_at=stamp) if existing else None
    monkeypatch.setattr(store, '_get', lambda *args: old)
    collection.find_one.return_value = old.bson() if old else None
    for name in ('insert_one', 'update_one'):
        getattr(collection, name).side_effect = OperationFailure('DO_NOT_LOG_SECRET', code=code)
    service.destination = store
    report = service.run('users')['users']
    if code == 121:
        assert report['status'] == 'PARTIAL' and report['failed'] == 0
        assert report['conflict_reasons'] == {'MONGO_METADATA_DOCUMENT_INVALID': 1}
    else:
        assert report['status'] == 'FAILED' and report['error_code'] == 'TARGET_MONGO_WRITE_FAILED'
    assert 'DO_NOT_LOG_SECRET' not in caplog.text


@pytest.mark.parametrize('flag,code', [
    ('source_mongo_enabled', 'SHARED_MONGO_CONFLICT_SOURCE_ENABLED'),
    ('master_mongo_identity_bootstrap', 'SHARED_MONGO_CONFLICT_IDENTITY_BOOTSTRAP'),
])
def test_shared_config_conflict_fails_before_clients_or_storage(container, monkeypatch, flag, code):
    create = MagicMock()
    monkeypatch.setattr('data_sync_etl.composition.create_source', create)
    settings = container.settings.model_copy(update={flag: True, 'mongo_metadata_mode': 'shared'})
    with pytest.raises(DomainError, match=code):
        Container(settings, container.engine)
    create.assert_not_called()


@pytest.mark.parametrize('payload', [
    {}, {'bio': None}, {'bio': '   '}, {'organization_name': None, 'subject_id': None},
])
def test_shared_identity_null_or_blank_extensions_are_not_matched(container, payload):
    old, source, writer = configured(container)
    source.get_user.return_value = UserProfileDocument(_id='MUSER', created_at=now(), updated_at=now(), **payload)
    service = SourceMongoMetadataSync(container.uow, source, None, shared=True,
                                     reference_guard=old.reference_guard, subject_guard=old.subject_guard)
    report = service.run('users')['users']
    assert report['mode'] == 'VALIDATE_SHARED' and report['missing_extension'] == 1
    assert report['matched'] == report['inserted'] == report['updated'] == 0
    source.assert_distinct_destination.assert_not_called()
    writer.sync_source_metadata.assert_not_called()


def test_shared_reports_missing_invalid_match_and_outage_separately(container, caplog):
    old, source, writer = configured(container)
    service = SourceMongoMetadataSync(container.uow, source, None, shared=True,
                                     reference_guard=old.reference_guard, subject_guard=old.subject_guard)
    assert service.run('users')['users']['matched'] == 1
    source.get_user.return_value = None
    assert service.run('users')['users']['missing_document'] == 1
    source.get_user.side_effect = DomainError('PRIVATE_BAD_PAYLOAD')
    report = service.run('users')['users']
    assert report['status'] == 'PARTIAL' and report['invalid_document'] == 1
    source.get_user.side_effect = ConnectionError('PRIVATE_BAD_URI')
    report = service.run('users')['users']
    assert report['status'] == 'FAILED' and report['error_code'] == 'SHARED_MONGO_READ_FAILED'
    assert 'PRIVATE_BAD' not in caplog.text
    writer.sync_source_metadata.assert_not_called()
    with pytest.raises(ValueError, match='does not accept a writer'):
        SourceMongoMetadataSync(container.uow, source, writer, shared=True)


def test_shared_uses_only_one_database_and_pg_hooks_never_scan(container, monkeypatch):
    mongo = MagicMock()
    factory = MagicMock(return_value=mongo)
    store = MagicMock(spec=MetadataDocumentPort)
    monkeypatch.setattr('data_sync_etl.composition.MongoClient', factory)
    monkeypatch.setattr('data_sync_etl.composition.MongoMetadataStore', lambda _: store)
    settings = container.settings.model_copy(update={'mongo_metadata_mode': 'shared',
        'source_mongo_database': 'OBSOLETE_DO_NOT_ACCESS', 'source_mongo_uri': 'OBSOLETE_DO_NOT_CONNECT'})
    runtime = Container(settings, container.engine)
    try:
        runtime.sync.run(full=True)
        runtime.sync.run(full=False)
        assert factory.call_count == 1
        assert [call.args for call in mongo.__getitem__.call_args_list] == [(settings.mongo_database,)]
        assert runtime.source_mongo_client is None
        assert runtime.mongo_metadata_sync.destination is None
        assert runtime.master_metadata_projection is runtime.mongo_metadata_sync
        assert not store.mock_calls
    finally:
        runtime.close()


def test_shared_cli_config_conflict_has_safe_actionable_message(monkeypatch, capsys):
    import data_sync_etl.cli.main as cli

    def conflict():
        raise DomainError('SHARED_MONGO_CONFLICT_IDENTITY_BOOTSTRAP: PRIVATE_URI_OR_PAYLOAD')

    monkeypatch.setattr(cli, 'Container', conflict)
    monkeypatch.setattr(cli.sys, 'argv', ['etl', 'sync-mongo-metadata', '--full'])
    assert cli.main() == 1
    output = capsys.readouterr().err
    assert 'MASTER_MONGO_IDENTITY_BOOTSTRAP=false' in output and 'PRIVATE_' not in output
