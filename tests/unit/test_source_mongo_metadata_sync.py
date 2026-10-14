from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.source.mongo_metadata import MongoMetadataSourceReader
from data_sync_etl.application.source_mongo_metadata_sync import SourceMongoMetadataSync
from data_sync_etl.cli.main import parser
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import UserProfileDocument
from data_sync_etl.ports.mongo_foundation import MetadataDocumentPort, SourceMetadataPort


def test_defaults_disabled_and_cli_full_only():
    settings = Settings(_env_file=None)
    assert not settings.source_mongo_enabled and settings.source_mongo_database == 'source_metadata'
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
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.sync_upsert('master_users', {'id': 'user-1', 'mongo_id': 'MUSER'})
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
