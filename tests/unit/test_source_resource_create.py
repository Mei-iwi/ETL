import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.source.postgresql_writeback import PostgreSQLSourceMetadataWriter
from data_sync_etl.application.source_resource_create import (
    APPLIED,
    PENDING,
    SourceCreateWriteBackError,
    SourceResourceCreateService,
)
from data_sync_etl.application.source_resource_delete import SourceResourceDeleteService
from data_sync_etl.domain.core import now
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash


@pytest.mark.parametrize('empty', ['', '   ', None])
def test_optional_create_catalog_fields_normalize_empty_form_values(empty):
    payload = SourceResourceCreateService._validate_payload({
        'provider_id': 'p1', 'resource_type_id': 't1', 'title': 'Scan',
        'publication_status': 'DRAFT', 'subject_id': empty, 'grade_level_id': empty,
    })
    assert payload['subject_id'] is None and payload['grade_level_id'] is None


class Repo:
    def __init__(self):
        locator = SourceLocator(
            backend='postgresql', namespace='postgresql:learning',
            entity_type='users', source_record_id='USR260100001',
        )
        self.rows = {
            ('master_users', 'master-user-1'): {
                'id': 'master-user-1', 'source_origin': 'POSTGRESQL', 'is_active': True,
                'deleted_at': None, 'source_missing_at': None,
                'source_identity_version': locator.version, 'source_backend': locator.backend,
                'source_namespace': locator.namespace, 'source_entity_type': locator.entity_type,
                'source_record_key': locator.source_record_id,
                'source_identity_hash': source_identity_hash(locator),
            }
        }
        self.jobs = {}

    def get(self, table, key, *, lock=False):
        return self.rows.get((table, key)) or self.jobs.get(key)

    def find(self, table, filters=None, *, order=(), limit=None, lock=False, skip_locked=False):
        filters = filters or {}
        rows = [row for row in self.jobs.values()
                if all(row.get(key) == value for key, value in filters.items())]
        if order:
            rows.sort(key=lambda row: tuple(row.get(key) for key in order))
        return rows[:limit] if limit else rows

    def insert(self, table, values):
        row = dict(values)
        self.jobs[row['id']] = row
        return row

    def update(self, table, key, values):
        self.jobs[key].update(values)


class Uow:
    def __init__(self):
        self.repo = Repo()

    def __call__(self):
        class Context:
            def __enter__(_self):
                return self.repo

            def __exit__(_self, *args):
                return False

        return Context()


class Writer:
    supported = True
    create_supported = True

    def __init__(self):
        self.ids = []
        self.calls = []
        self.fail_once = False
        self.fail_reserve_once = False
        self.fail_before_commit_once = False

    def allows_create(self, **kwargs):
        return True

    def reserve_resource_id(self, *, created_at):
        if self.fail_reserve_once:
            self.fail_reserve_once = False
            raise RuntimeError('counter unavailable before allocation')
        return 'RES260100001'

    def create_resource(self, **kwargs):
        if self.fail_before_commit_once:
            self.fail_before_commit_once = False
            raise RuntimeError('SOURCE transaction rolled back')
        self.calls.append(kwargs['source_resource_id'])
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError('connection dropped after SOURCE commit')
        return {
            'status': 'APPLIED', 'source_resource_id': kwargs['source_resource_id'],
            'source_state_hash': 'a' * 64, 'idempotent_replay': len(self.calls) > 1,
        }


class IncrementingWriter(Writer):
    def reserve_resource_id(self, *, created_at):
        self.ids.append(len(self.ids) + 1)
        return f'RES26010000{self.ids[-1]:02d}'


def crash_checkpoint(repo, key, values):
    if values.get('checkpoint', {}).get('source_resource_id'):
        raise SystemExit('crash before checkpoint commit')
    repo.jobs[key].update(values)


def settings():
    return SimpleNamespace(
        source_metadata_writeback_enabled=True,
        source_metadata_writer_mode='postgresql',
        source_metadata_writeback_max_attempts=3,
        reconciliation_lease_seconds=60,
    )


def payload():
    return {
        'provider_id': 'provider-1', 'resource_type_id': 'type-1',
        'title': 'Metadata only', 'publication_status': 'DRAFT',
    }


def test_actor_mapping_requires_existing_master_source_identity():
    service = SourceResourceCreateService(Uow(), Writer(), settings())
    with pytest.raises(SourceCreateWriteBackError) as exc:
        service.request(payload(), idempotency_key='create-actor-1', actor_id='local-admin', role='ADMIN')
    assert exc.value.code == 'SOURCE_CREATE_ACTOR_MAPPING_REQUIRED'


def test_create_is_durable_and_retries_same_source_id_after_uncertain_commit():
    uow, writer = Uow(), Writer()
    service = SourceResourceCreateService(uow, writer, settings())
    pending = service.request(payload(), idempotency_key='create-resource-1',
                              actor_id='master:master-user-1', role='ADMIN')
    assert pending['status'] == PENDING
    writer.fail_once = True
    assert service.process_pending_once('test-create-worker')
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == PENDING
    assert row['checkpoint']['source_resource_id'] == 'RES260100001'
    assert service.process_pending_once('test-create-worker')
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == APPLIED
    assert writer.calls == ['RES260100001', 'RES260100001']


def test_explicit_failed_create_resubmission_reuses_reserved_identity():
    uow, writer = Uow(), Writer()
    service = SourceResourceCreateService(uow, writer, settings())
    first = service.request(payload(), idempotency_key='failed-resubmit-01',
                            actor_id='master:master-user-1', role='ADMIN')
    row = uow.repo.jobs[first['request_id']]
    row.update(status='SOURCE_CREATE_FAILED', attempts=3, error_code='SOURCE_CREATE_FAILED')
    row['checkpoint'].update(source_resource_id='RES260100001',
                             reserved_at=now().isoformat(), error_code='SOURCE_CREATE_FAILED')
    retried = service.request(payload(), idempotency_key='failed-resubmit-01',
                              actor_id='master:master-user-1', role='ADMIN')
    assert retried['request_id'] == first['request_id']
    assert retried['status'] == PENDING
    assert retried['source_resource_id'] == 'RES260100001'
    assert service.process_pending_once(request_id=first['request_id'])
    assert row['status'] == APPLIED
    assert len(uow.repo.jobs) == 1


def test_worker_recovers_when_counter_fails_before_id_allocation():
    uow, writer = Uow(), Writer()
    service = SourceResourceCreateService(uow, writer, settings())
    service.request(payload(), idempotency_key='create-before-id-1',
                    actor_id='master:master-user-1', role='ADMIN')
    writer.fail_reserve_once = True
    assert service.process_pending_once('test-create-worker')
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == PENDING
    assert 'source_resource_id' not in row['checkpoint']
    assert service.process_pending_once('test-create-worker')
    assert row['status'] == APPLIED
    assert writer.calls == ['RES260100001']


def test_worker_retries_same_id_when_source_transaction_rolls_back_before_commit():
    uow, writer = Uow(), Writer()
    service = SourceResourceCreateService(uow, writer, settings())
    service.request(payload(), idempotency_key='create-before-commit-1',
                    actor_id='master:master-user-1', role='ADMIN')
    writer.fail_before_commit_once = True
    assert service.process_pending_once('test-create-worker')
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == PENDING
    assert row['checkpoint']['source_resource_id'] == 'RES260100001'
    assert service.process_pending_once('test-create-worker')
    assert row['status'] == APPLIED
    assert writer.calls == ['RES260100001']


def test_replay_after_master_reconciliation_boundary_does_not_create_again():
    uow, writer = Uow(), Writer()
    service = SourceResourceCreateService(uow, writer, settings())
    service.request(payload(), idempotency_key='create-before-sync-1',
                    actor_id='master:master-user-1', role='ADMIN')
    assert service.process_pending_once('test-create-worker')
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == APPLIED
    assert row['checkpoint']['reconciliation_required'] is True
    assert service.process_pending_once('test-create-worker') is False
    assert writer.calls == ['RES260100001']


def test_create_with_file_uses_one_durable_job_and_reuses_source_id(tmp_path):
    class Version:
        def __init__(self):
            self.calls = []

        def apply_for_source_resource(self, *, checkpoint, stager, persist_checkpoint):
            self.calls.append(checkpoint['source_resource_id'])
            checkpoint['source_version_id'] = 'RSV2601000001'
            checkpoint['workflow'] = 'SOURCE_VERSION_COMMITTED'
            persist_checkpoint(checkpoint)
            return {'status': 'APPLIED', 'source_state_hash': 'b' * 64}

    class Stager:
        pass

    uow, writer, version = Uow(), Writer(), Version()
    service = SourceResourceCreateService(uow, writer, settings(), version_service=version, stager=Stager())
    manifest = {
        'path': str(tmp_path / 'fixture.pdf'), 'filename': 'fixture.pdf',
        'content_type': 'application/pdf', 'file_size': 10, 'source_hash': 'c' * 64,
    }
    pending = service.request(payload(), idempotency_key='create-with-file-1',
                              actor_id='master:master-user-1', role='ADMIN', source_file=manifest)
    assert pending['status'] == PENDING
    assert service.process_pending_once('test-create-worker')
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == APPLIED
    assert row['checkpoint']['source_version_id'] == 'RSV2601000001'
    assert version.calls == ['RES260100001']
    replay = service.request(payload(), idempotency_key='create-with-file-1',
                             actor_id='master:master-user-1', role='ADMIN', source_file=manifest)
    assert replay['status'] == APPLIED
    assert version.calls == ['RES260100001']


def test_crash_after_id_reservation_before_checkpoint_recovers_without_duplicate():
    uow, writer = Uow(), IncrementingWriter()
    original_update = uow.repo.update
    uow.repo.update = lambda table, key, values: crash_checkpoint(uow.repo, key, values)
    service = SourceResourceCreateService(uow, writer, settings())
    service.request(payload(), idempotency_key='create-after-id-1',
                    actor_id='master:master-user-1', role='ADMIN')
    with pytest.raises(SystemExit, match='crash before checkpoint commit'):
        service.process_pending_once('test-create-worker')
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == 'SOURCE_CREATE_PROCESSING'
    assert 'source_resource_id' not in row['checkpoint']
    # Simulate the lease expiring after the process died.  The first reserved
    # counter value is lost, but no SOURCE resource existed yet.
    row['lease_expires_at'] = now()
    uow.repo.update = original_update
    assert service.process_pending_once('test-create-worker')
    assert row['status'] == APPLIED
    assert writer.calls == ['RES2601000002']


def test_contract_gate_fails_closed_before_actor_or_source_call():
    writer = Writer()
    writer.create_supported = False
    service = SourceResourceCreateService(Uow(), writer, settings())
    with pytest.raises(SourceCreateWriteBackError) as exc:
        service.request(payload(), idempotency_key='create-contract-1',
                        actor_id='master:master-user-1', role='ADMIN')
    assert exc.value.code == 'SOURCE_CREATE_CONTRACT_UNAVAILABLE'


def test_postgresql_writer_requires_explicit_create_column_contract(tmp_path):
    contract = {
        'backend': 'postgresql', 'namespace': 'postgresql:learning',
        'entity_type': 'resources', 'schema': 'public', 'table': 'learning_resources',
        'id_column': 'id', 'actions': ['update', 'create'],
        'allowed_roles': ['ADMIN'], 'allowed_actor_ids': [],
        'state_columns': [
            'id', 'mongo_id', 'provider_id', 'resource_type_id', 'created_by', 'title',
            'publication_status', 'is_active', 'deleted_at', 'deleted_by', 'created_at',
            'updated_at', 'provider_name', 'provider_type', 'provider_is_active',
            'provider_deleted_at', 'provider_deleted_by', 'provider_created_at',
            'provider_updated_at', 'resource_type_code', 'resource_type_name_vi',
            'resource_type_is_active', 'resource_type_deleted_at', 'resource_type_deleted_by',
            'resource_type_created_at', 'resource_type_updated_at',
        ],
        'columns': {'title': 'title'}, 'idempotency_column': None,
    }
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps(contract), encoding='utf-8')
    with pytest.raises(ValueError, match='SOURCE_WRITE_CREATE_CONTRACT_INCOMPLETE'):
        PostgreSQLSourceMetadataWriter('postgresql+psycopg://localhost/test', path)


def test_postgresql_writer_cannot_enable_contract_action_without_env_allowlist(tmp_path):
    contract = {
        'backend': 'postgresql', 'namespace': 'postgresql:learning',
        'entity_type': 'resources', 'schema': 'public', 'table': 'learning_resources',
        'id_column': 'id', 'actions': ['create'], 'allowed_roles': ['ADMIN'],
        'allowed_actor_ids': [],
        'state_columns': [
            'id', 'mongo_id', 'provider_id', 'resource_type_id', 'created_by', 'title',
            'publication_status', 'is_active', 'deleted_at', 'deleted_by', 'created_at',
            'updated_at', 'provider_name', 'provider_type', 'provider_is_active',
            'provider_deleted_at', 'provider_deleted_by', 'provider_created_at',
            'provider_updated_at', 'resource_type_code', 'resource_type_name_vi',
            'resource_type_is_active', 'resource_type_deleted_at', 'resource_type_deleted_by',
            'resource_type_created_at', 'resource_type_updated_at',
        ],
        'columns': {
            'id': 'id', 'provider_id': 'provider_id', 'resource_type_id': 'resource_type_id',
            'created_by': 'created_by', 'title': 'title', 'publication_status': 'publication_status',
            'is_active': 'is_active', 'created_at': 'created_at', 'updated_at': 'updated_at',
        },
    }
    path = tmp_path / 'contract.json'
    path.write_text(json.dumps(contract), encoding='utf-8')
    with pytest.raises(ValueError, match='SOURCE_WRITE_ACTIONS_CONFIGURATION_MISMATCH'):
        PostgreSQLSourceMetadataWriter(
            'postgresql+psycopg://localhost/test', path, allowed_actions=('update',))


def test_staging_proposal_contract_validates_without_loading_private_secret():
    path = Path('deploy/config/source-create-write-contract.staging.proposal.json')
    writer = PostgreSQLSourceMetadataWriter(
        'postgresql+psycopg://localhost/test', path, allowed_actions=('create',))
    assert writer.create_supported is True
    assert writer.actions == {'create'}


def test_source_delete_is_contract_gated_and_recovery_safe():
    uow, writer = Uow(), Writer()
    writer.delete_supported = True
    writer.delete_calls = []
    writer.allows_delete = lambda **kwargs: True
    writer.delete_resource = lambda **kwargs: (
        writer.delete_calls.append(kwargs['locator'].source_record_id)
        or {'status': 'APPLIED', 'source_state_hash': 'b' * 64, 'idempotent_replay': False}
    )
    locator = SourceLocator(
        backend='postgresql', namespace='postgresql:learning',
        entity_type='resources', source_record_id='RES260100001',
    )
    uow.repo.rows[('master_learning_resources', 'resource-1')] = {
        'id': 'resource-1', 'source_origin': 'POSTGRESQL', 'is_active': True,
        'deleted_at': None, 'source_state_hash': 'a' * 64,
        'source_identity_version': locator.version, 'source_backend': locator.backend,
        'source_namespace': locator.namespace, 'source_entity_type': locator.entity_type,
        'source_record_key': locator.source_record_id, 'source_identity_hash': source_identity_hash(locator),
    }
    service = SourceResourceDeleteService(uow, writer, settings())
    pending = service.request('resource-1', expected_source_state_hash='a' * 64,
                              idempotency_key='delete-resource-1',
                              actor_id='master:master-user-1', role='ADMIN', approve=False)
    assert pending['status'] == 'SOURCE_DELETE_PENDING'
    assert service.process_pending_once('delete-worker') is False
    approved = service.request('resource-1', expected_source_state_hash='a' * 64,
                               idempotency_key='delete-resource-1',
                               actor_id='master:master-user-1', role='ADMIN', approve=True)
    assert approved['status'] == 'SOURCE_DELETE_PENDING'
    assert service.process_pending_once('delete-worker') is True
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == 'SOURCE_DELETE_APPLIED'
    assert writer.delete_calls == ['RES260100001']
    assert service.process_pending_once('delete-worker') is False
