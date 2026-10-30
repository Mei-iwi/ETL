"""Regression evidence for the five-commit audit; isolated MASTER and fake SOURCE."""

import pytest

from data_sync_etl.application.source_metadata_writeback import SourceWriteBackError
from data_sync_etl.domain.core import DomainError, now
from tests.unit.test_source_metadata_writeback import prepare_writeback
from tests.unit.test_source_resolution import seed_source_mapping


def command(container, resource, **overrides):
    values = dict(expected_source_state_hash=resource['source_state_hash'],
                  idempotency_key='audit-writeback-01', actor_id='local-admin', role='ADMIN')
    values.update(overrides)
    return container.source_writeback.request('resource-1', {'title': 'Updated title'}, **values)


def test_approve_cannot_create_and_apply_in_one_step(container):
    resource, writer = prepare_writeback(container)
    with pytest.raises(SourceWriteBackError, match='APPROVAL_REQUIRED'):
        container.source_writeback.request(
            'resource-1', {'title': 'Unstaged'}, actor_id='local-admin', role='ADMIN',
            expected_source_state_hash=resource['source_state_hash'],
            idempotency_key='unstaged-approve', approve=True)
    assert writer.records['resource-1']['title'] == resource['title']


def test_missing_actor_cannot_mint_admin_writeback(container):
    resource, _ = prepare_writeback(container)
    with pytest.raises(SourceWriteBackError):
        container.source_writeback.request(
            'resource-1', {'title': 'Forged'}, actor_id='', role='ADMIN',
            expected_source_state_hash=resource['source_state_hash'],
            idempotency_key='missing-actor', dry_run=True)


def test_resolution_rejects_foreign_source_namespace(container, monkeypatch):
    from data_sync_etl.domain.search_access import SearchAccess

    source = seed_source_mapping(container)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'source_namespace': 'foreign'})
    calls = []
    monkeypatch.setattr(container.source, 'get_version', lambda key: calls.append(key) or source, raising=False)
    result = container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))
    assert result['resolution_status'] == 'blocked'
    assert not calls


def test_legacy_complete_null_identity_can_sync_after_additive_migration(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    with container.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        repo.update('master_learning_resources', row['id'], {key: None for key in (
            'source_identity_version', 'source_backend', 'source_namespace',
            'source_entity_type', 'source_record_key', 'source_identity_hash')})
    container.sync.run('resources', full=True)
    with container.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-1')['source_identity_hash']


def test_partial_identity_still_fails_closed(container):
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'source_identity_hash': None})
    with pytest.raises(DomainError, match='BLOCKED_SOURCE_IDENTITY'):
        container.sync.run('resources', full=True)


@pytest.mark.parametrize('change', ['revoked', 'archived', 'missing', 'drift', 'identity', 'namespace'])
def test_writeback_revocation_lifecycle_and_mapping_cannot_write(container, change):
    resource, writer = prepare_writeback(container)
    command(container, resource)
    if change == 'revoked':
        container.source_writeback.permission = lambda **_: False
    else:
        fields = {'archived': {'publication_status': 'ARCHIVED'},
                  'missing': {'source_missing_at': now()},
                  'drift': {'title': 'Unauthorized local edit'},
                  'identity': {'source_identity_hash': None},
                  'namespace': {'source_namespace': 'different-source'}}[change]
        with container.uow() as repo:
            repo.update('master_learning_resources', 'resource-1', fields)
    with pytest.raises(SourceWriteBackError):
        command(container, resource, approve=True)
    assert not writer._replays


def test_writeback_dry_run_no_grant_and_payload_reuse(container):
    resource, writer = prepare_writeback(container)
    assert command(container, resource, dry_run=True)['status'] == 'DRY_RUN'
    with container.uow() as repo:
        assert not repo.find('reconciliation_audit_jobs')
    command(container, resource)
    with pytest.raises(SourceWriteBackError, match='IDEMPOTENCY_REUSED'):
        container.source_writeback.request('resource-1', {'title': 'Different'},
            expected_source_state_hash=resource['source_state_hash'], idempotency_key='audit-writeback-01',
            actor_id='local-admin', role='ADMIN', approve=True)
    container.source_writeback.permission = None
    with pytest.raises(SourceWriteBackError, match='FORBIDDEN'):
        command(container, resource, approve=True)
    assert not writer._replays


def test_writeback_retries_have_exact_limit_and_sanitized_errors(container, monkeypatch):
    resource, writer = prepare_writeback(container)
    command(container, resource)
    calls = []
    def unavailable(**_):
        calls.append(1)
        raise DomainError('postgresql://writer:PRIVATE@internal secret error')
    monkeypatch.setattr(writer, 'write_metadata', unavailable)
    maximum = container.settings.source_metadata_writeback_max_attempts
    for _ in range(maximum):
        with pytest.raises(SourceWriteBackError) as error:
            command(container, resource, approve=True)
        assert 'PRIVATE' not in str(error.value)
    with pytest.raises(SourceWriteBackError, match='RETRY_EXHAUSTED'):
        command(container, resource, approve=True)
    assert len(calls) == maximum
    with container.uow() as repo:
        row, = repo.find('reconciliation_audit_jobs')
        assert row['status'] == 'WRITEBACK_FAILED' and row['attempts'] == maximum
        assert 'PRIVATE' not in str(row)


def test_concurrent_approval_and_replay_write_once(container, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    resource, writer = prepare_writeback(container)
    command(container, resource)
    started, release = Event(), Event()
    original = writer.write_metadata
    def slow(**kwargs):
        started.set()
        assert release.wait(10)
        return original(**kwargs)
    monkeypatch.setattr(writer, 'write_metadata', slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(command, container, resource, approve=True)
        assert started.wait(10)
        try:
            with pytest.raises(SourceWriteBackError, match='BUSY'):
                command(container, resource, approve=True)
        finally:
            release.set()
        result = first.result(10)
    assert command(container, resource, approve=True) == result
    assert len(writer._replays) == 1


def test_partial_acknowledgement_retries_writer_idempotently(container, monkeypatch):
    resource, writer = prepare_writeback(container)
    command(container, resource)
    original = writer.write_metadata
    def ambiguous(**kwargs):
        original(**kwargs)
        raise TimeoutError('lost ACK')
    monkeypatch.setattr(writer, 'write_metadata', ambiguous)
    with pytest.raises(SourceWriteBackError):
        command(container, resource, approve=True)
    monkeypatch.setattr(writer, 'write_metadata', original)
    assert command(container, resource, approve=True)['status'] == 'WRITEBACK_APPLIED'
    assert len(writer._replays) == 1


def test_outbox_never_enters_audit_worker(container):
    from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
    resource, _ = prepare_writeback(container)
    command(container, resource)
    assert AuditQueue(container.uow, container.settings).claim() is None


def test_granted_operator_must_own_the_resource(container):
    from data_sync_etl.domain.source_ownership import state_hash
    resource, writer = prepare_writeback(container)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'created_by': 'teacher'})
        resource = repo.get('master_learning_resources', 'resource-1')
        repo.update('master_learning_resources', 'resource-1', {'source_state_hash': state_hash(resource)})
        resource = repo.get('master_learning_resources', 'resource-1')
    writer.records['resource-1']['source_state_hash'] = resource['source_state_hash']
    container.source_writeback.permission = lambda **grant: grant['actor_id'] == 'master:teacher'
    args = dict(actor_id='master:teacher', role='OPERATOR', owner_id='teacher')
    assert command(container, resource, **args)['status'] == 'WRITEBACK_PENDING_APPROVAL'
    assert command(container, resource, approve=True, **args)['status'] == 'WRITEBACK_APPLIED'


def test_source_cas_rejects_concurrent_source_update(container):
    resource, writer = prepare_writeback(container)
    command(container, resource)
    writer.records['resource-1'].update(title='Newer SOURCE title', source_state_hash='c' * 64)
    with pytest.raises(SourceWriteBackError, match='CONFLICT'):
        command(container, resource, approve=True)
    assert writer.records['resource-1']['title'] == 'Newer SOURCE title'
    with container.uow() as repo:
        row, = repo.find('reconciliation_audit_jobs')
        assert row['status'] == 'WRITEBACK_CONFLICT'


@pytest.mark.parametrize('kind', ['hash', 'missing', 'offline', 'blocked', 'wrong_resource', 'failed_import'])
def test_resolution_live_source_fail_closed(container, monkeypatch, kind):
    from data_sync_etl.domain.search_access import SearchAccess
    source = seed_source_mapping(container)
    if kind == 'hash':
        source['source_hash'] = 'c' * 64
    elif kind == 'wrong_resource':
        source['resource_id'] = 'other'
    elif kind == 'blocked':
        source['resource_publication_status'] = 'DRAFT'
    elif kind == 'failed_import':
        with container.uow() as repo:
            row, = repo.find('source_file_imports')
            repo.update('source_file_imports', row['id'], {'status': 'FAILED'})
    def lookup(_):
        if kind == 'offline':
            raise RuntimeError('postgresql://PRIVATE')
        return None if kind == 'missing' else source
    monkeypatch.setattr(container.source, 'get_version', lookup, raising=False)
    result = container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))
    assert not result['source_record_available']
    assert result['resolution_status'] == {'hash': 'stale', 'wrong_resource': 'stale',
        'blocked': 'blocked', 'failed_import': 'blocked', 'missing': 'unavailable', 'offline': 'unavailable'}[kind]
    assert 'PRIVATE' not in str(result)


def test_resolution_version_mismatch_and_lifecycle(container):
    from data_sync_etl.application.source_resolution import SourceResolutionError
    from data_sync_etl.domain.search_access import SearchAccess
    seed_source_mapping(container)
    with pytest.raises(SourceResolutionError):
        container.source_resolution.resolve('resource-1', SearchAccess('local_demo'), 'missing-version')
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'is_active': False})
    with pytest.raises(SourceResolutionError):
        container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))


def test_resolution_rechecks_authority_after_network_read(container, monkeypatch):
    from data_sync_etl.application.source_resolution import SourceResolutionError
    from data_sync_etl.domain.search_access import SearchAccess

    source = seed_source_mapping(container)
    def lookup(_):
        with container.uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'publication_status': 'DRAFT'})
        return source
    monkeypatch.setattr(container.source, 'get_version', lookup, raising=False)
    with pytest.raises(SourceResolutionError, match='RESOURCE_NOT_FOUND'):
        container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))


def test_resolution_never_echoes_malformed_identity(container):
    from data_sync_etl.domain.search_access import SearchAccess
    seed_source_mapping(container)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'source_identity_hash': 'postgresql://PRIVATE'})
    result = container.source_resolution.resolve('resource-1', SearchAccess('local_demo'))
    assert result['resolution_status'] == 'blocked' and result['source_identity'] is None
    assert 'PRIVATE' not in str(result)
