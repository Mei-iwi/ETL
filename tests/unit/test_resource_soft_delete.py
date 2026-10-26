"""SQLite transactions and real search pipelines; embedding doubles are explicit."""
from copy import deepcopy

import pytest

from data_sync_etl.application.resource_deletion import ResourceDeletion
from data_sync_etl.domain.core import now
from data_sync_etl.domain.source_ownership import state_hash
from data_sync_etl.security.auth import AuthError, Principal
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_metadata_search import search

ADMIN = Principal('admin-fixture', 'ADMIN', 'single_admin', 'test')


def deletion(c, **kwargs):
    return ResourceDeletion(c.uow).change('resource-1', ADMIN, **kwargs)


def test_local_delete_restore_revision_audit_and_physical_preservation(metadata):
    c, version, _, service = metadata
    service.reconcile(apply=True, dense=True)
    before = deepcopy(service.store.rows)
    first = deletion(c, reason='Synthetic deletion')
    assert first == {'resource_id': 'resource-1', 'deleted': True, 'revision': 1, 'changed': True}
    assert not deletion(c)['changed']
    assert search(c)['total'] == 0
    with c.uow() as repo:
        resource = repo.get('master_learning_resources', 'resource-1')
        assert resource['master_deleted_at'].tzinfo and resource['deleted_at'] is None
        assert repo.get('resource_versions', version['id'])['lifecycle_status'] == 'INGESTED'
        assert len(repo.find('resource_deletion_events')) == 1
        assert repo.get_resource_detail('resource-1') is None
        rows, total = repo.list_resources(keyword=None, subject_id=None, grade_level_id=None,
            resource_type_code=None, offset=0, limit=100)
        assert total == len(rows) and all(row['id'] != 'resource-1' for row in rows)
    assert service.store.rows == before  # API transaction did not touch Mongo.
    assert deletion(c, restore=True, expected_revision=1)['revision'] == 2
    assert not deletion(c, restore=True)['changed']
    assert search(c)['total'] == 0  # Old projection is fenced, not blindly reused.
    assert service.reconcile(apply=True, dense=True)['embeds'] == 0
    assert search(c)['total'] == 1
    with c.uow() as repo:
        events = repo.find('resource_deletion_events', order=('revision',))
        assert [event['action'] for event in events] == ['DELETE', 'RESTORE']
        assert events[0]['reason'] == 'Synthetic deletion' and events[0]['actor_id'] == ADMIN.id


@pytest.mark.parametrize('extension', ['pdf', 'docx', 'xlsx', 'pptx', 'png', 'jpg', 'mp3', 'mp4'])
@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_every_metadata_mode_hides_registered_formats_and_totals(metadata, extension, method):
    c, version, _, service = metadata
    with c.uow() as repo:
        repo.update('resource_versions', version['id'], {'storage_object_key': 'file.' + extension})
    service.reconcile(apply=True, dense=True)
    assert search(c, method=method)['total'] == 1
    deletion(c)
    for page in (1, 2):
        result = search(c, method=method, page=page, size=1)
        assert result['items'] == [] and result['total'] == result['total_pages'] == 0
    with c.uow() as repo:
        assert repo.get('resource_versions', version['id']) is not None


@pytest.mark.parametrize('actor', [None, Principal('owner', 'VIEWER', 'master_users', 'test'),
                                  Principal('op', 'OPERATOR', 'db_users', 'test')])
def test_non_admin_cannot_delete_or_restore(metadata, actor):
    c, _, _, _ = metadata
    for restore in (False, True):
        with pytest.raises(AuthError) as error:
            ResourceDeletion(c.uow).change('resource-1', actor, restore=restore)
        assert error.value.status == 403
    with c.uow() as repo:
        assert not repo.find('resource_deletion_events')


def test_source_sync_never_changes_local_fence_or_hash_contract(metadata):
    c, _, _, _ = metadata
    with c.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        values = {k: v for k, v in row.items() if k not in ('source_origin', 'source_state_hash', 'source_missing_at')}
        repo.update('master_learning_resources', row['id'], {'source_origin': 'POSTGRESQL', 'source_state_hash': state_hash(row)})
    deletion(c)
    with c.uow() as repo:
        assert repo.sync_upsert('master_learning_resources', values, source_origin='POSTGRESQL') == 'SKIPPED'
        values.update(title='New source title', updated_at=now(), deletion_revision=0, master_deleted_at=None)
        assert repo.sync_upsert('master_learning_resources', values, source_origin='POSTGRESQL') == 'UPDATED'
        row = repo.get('master_learning_resources', 'resource-1')
        assert row['master_deleted_at'] and row['deletion_revision'] == 1
        assert row['source_state_hash'] == state_hash(row)
        repo.update('master_learning_resources', row['id'], {'deleted_at': now()})
    with pytest.raises(AuthError, match='RESOURCE_RESTORE_SOURCE_RESTRICTED'):
        deletion(c, restore=True)


def test_restore_preserves_source_policy_and_revision_conflicts(metadata):
    c, _, _, _ = metadata
    deletion(c)
    with pytest.raises(AuthError, match='RESOURCE_DELETION_CONFLICT'):
        deletion(c, restore=True, expected_revision=0)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'publication_status': 'DRAFT'})
    with pytest.raises(AuthError, match='RESOURCE_RESTORE_SOURCE_RESTRICTED'):
        deletion(c, restore=True)


def test_durable_outbox_survives_mongo_failure_and_restart(metadata, monkeypatch):
    c, version, _, service = metadata
    service.reconcile(apply=True)
    deletion(c)
    invalidate = service.store.invalidate
    def unavailable(_):
        raise RuntimeError('Synthetic Mongo outage')
    monkeypatch.setattr(service.store, 'invalidate', unavailable)
    with pytest.raises(RuntimeError):
        service.propagate_deletions()
    assert search(c)['total'] == 0
    with c.uow() as repo:
        assert repo.find('resource_deletion_events')[0]['propagated_at'] is None
    monkeypatch.setattr(service.store, 'invalidate', invalidate)
    assert service.propagate_deletions() == 1
    assert service.propagate_deletions() == 0
    assert service.store.rows[version['id']]['status'] == 'STALE'


def test_delete_during_metadata_encoding_cannot_publish(metadata, monkeypatch):
    c, version, _, service = metadata
    encode = service.embedding.embed_documents
    def late(texts):
        output = encode(texts)
        deletion(c)
        return output
    monkeypatch.setattr(service.embedding, 'embed_documents', late)
    assert service.reconcile(apply=True, dense=True)['status'] == 'PARTIAL'
    assert version['id'] not in service.store.rows
    assert search(c)['items'] == []


def test_delete_while_final_authority_check_drops_all_old_metadata(metadata, monkeypatch):
    c, _, _, service = metadata
    service.reconcile(apply=True)
    validate = service.final_validate
    def deleted(*args, **kwargs):
        deletion(c)
        return validate(*args, **kwargs)
    monkeypatch.setattr(service, 'final_validate', deleted)
    assert search(c)['items'] == []


def test_audit_insert_failure_rolls_back_visibility(metadata, monkeypatch):
    from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
    c, _, _, _ = metadata
    insert = SQLAlchemyRepository.insert
    def fail(repo, table, values):
        if table == 'resource_deletion_events':
            raise RuntimeError('Synthetic audit write failure')
        return insert(repo, table, values)
    monkeypatch.setattr(SQLAlchemyRepository, 'insert', fail)
    with pytest.raises(RuntimeError):
        deletion(c)
    with c.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        assert row['master_deleted_at'] is None and row['deletion_revision'] == 0


def test_history_and_resource_hard_delete_are_guarded(metadata):
    from data_sync_etl.domain.core import DomainError
    c, _, _, _ = metadata
    deletion(c)
    for table in ('master_learning_resources', 'resource_versions', 'resource_deletion_events'):
        with pytest.raises(DomainError), c.uow() as repo:
            repo.delete(table, {})
    with pytest.raises(DomainError), c.uow() as repo:
        event = repo.find('resource_deletion_events')[0]
        repo.update('resource_deletion_events', event['id'], {'reason': 'replace history'})


def test_one_resource_tombstone_hides_all_historical_versions(metadata):
    from data_sync_etl.domain.core import DomainError
    c, version, _, _ = metadata
    with c.uow() as repo:
        second = repo.insert('resource_versions', {**version, 'id': 'qa-history-v2',
            'version_number': 2, 'parent_version_id': version['id'], 'source_hash': 'c' * 64})
    deletion(c)
    with pytest.raises(DomainError):
        c.resource_versions.run('resource-1')
    for row in (version, second):
        with pytest.raises(DomainError):
            c.source_resolver.resolve(row['id'])
    with c.uow() as repo:
        assert len(repo.find('resource_versions', {'resource_id': 'resource-1'})) == 2


def test_restore_noop_cannot_report_success_over_source_tombstone(metadata):
    c, _, _, _ = metadata
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'deleted_at': now()})
    with pytest.raises(AuthError, match='RESOURCE_RESTORE_SOURCE_RESTRICTED'):
        deletion(c, restore=True)
    with c.uow() as repo:
        assert repo.find('resource_deletion_events') == []
