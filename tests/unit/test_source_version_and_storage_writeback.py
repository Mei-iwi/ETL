import io
from types import SimpleNamespace

import pytest

from data_sync_etl.adapters.source.postgresql_version_writeback import (
    DisabledSourceVersionWriter,
    PostgreSQLSourceVersionWriter,
)
from data_sync_etl.adapters.storage.object import InMemoryObjectStorageAdapter
from data_sync_etl.adapters.storage.source_object_writer import SourceObjectStorageWriter
from data_sync_etl.application.source_create_staging import SourceCreateStager
from data_sync_etl.application.source_version_create import SourceVersionCreateService
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
from data_sync_etl.ports.cloud_storage import CloudObject


def test_source_version_contract_is_review_only_and_requires_explicit_action():
    path = 'deploy/config/source-version-write-contract.staging.proposal.json'
    writer = PostgreSQLSourceVersionWriter(
        'postgresql+psycopg://localhost/test', path, allowed_actions=('create',))
    assert writer.create_supported is True
    assert writer.id_prefix == 'RSV'
    assert DisabledSourceVersionWriter().create_supported is False
    with pytest.raises(DomainError, match='SOURCE_WRITE_ACTIONS_CONFIGURATION_MISMATCH'):
        PostgreSQLSourceVersionWriter(
            'postgresql+psycopg://localhost/test', path, allowed_actions=('update',))


def test_source_object_writer_verifies_checksum_and_is_idempotent(tmp_path):
    contract = tmp_path / 'source-storage.json'
    contract.write_text('''{
      "backend": "s3-compatible", "provider_id": "source-cloud-current",
      "bucket": "source-uploads", "actions": ["upload"],
      "allowed_roles": ["ADMIN"], "allowed_actor_ids": [],
      "prefix": "learning-resources/", "allowed_mime_types": ["application/pdf"],
      "max_upload_bytes": 4096, "private": true, "allow_overwrite": false
    }''', encoding='utf-8')
    cloud = InMemoryObjectStorageAdapter(provider_id='source-cloud-current', bucket='source-uploads')
    writer = SourceObjectStorageWriter(cloud, contract)
    payload = b'%PDF-1.7 source fixture'
    digest = __import__('hashlib').sha256(payload).hexdigest()
    first = writer.upload(io.BytesIO(payload), resource_id='RES260100001',
                          idempotency_key='source-upload-1', content_type='application/pdf',
                          expected_sha256=digest)
    second = writer.upload(io.BytesIO(b'ignored'), resource_id='RES260100001',
                           idempotency_key='source-upload-1', content_type='application/pdf',
                           expected_sha256=digest)
    assert first['status'] == second['status'] == 'OBJECT_VERIFIED'
    assert first['idempotent_replay'] is False
    assert second['idempotent_replay'] is True
    assert second['object'].sha256 == digest
    with pytest.raises(DomainError, match='SOURCE_STORAGE_CHECKSUM_MISMATCH'):
        writer.upload(io.BytesIO(payload), resource_id='RES260100001',
                      idempotency_key='source-upload-2', content_type='application/pdf',
                      expected_sha256='0' * 64)


def test_source_object_writer_rejects_mime_and_public_contract(tmp_path):
    contract = tmp_path / 'source-storage.json'
    contract.write_text('''{
      "backend": "s3-compatible", "provider_id": "source-cloud-current",
      "bucket": "source-uploads", "actions": ["upload"],
      "allowed_roles": ["ADMIN"], "allowed_actor_ids": [],
      "prefix": "learning-resources/", "allowed_mime_types": ["application/pdf"],
      "max_upload_bytes": 4096, "private": false, "allow_overwrite": false
    }''', encoding='utf-8')
    cloud = InMemoryObjectStorageAdapter(provider_id='source-cloud-current', bucket='source-uploads')
    with pytest.raises(DomainError, match='SOURCE_STORAGE_WRITE_POLICY_INVALID'):
        SourceObjectStorageWriter(cloud, contract)


class _Repo:
    def __init__(self):
        user_locator = SourceLocator(
            backend='postgresql', namespace='postgresql:learning',
            entity_type='users', source_record_id='USR260100001')
        resource_locator = SourceLocator(
            backend='postgresql', namespace='postgresql:learning',
            entity_type='resources', source_record_id='RES260100001')
        self.rows = {
            ('master_users', 'master-user-1'): {
                'id': 'master-user-1', 'source_origin': 'POSTGRESQL', 'is_active': True,
                'deleted_at': None, 'source_missing_at': None,
                'source_identity_version': user_locator.version, 'source_backend': user_locator.backend,
                'source_namespace': user_locator.namespace, 'source_entity_type': user_locator.entity_type,
                'source_record_key': user_locator.source_record_id,
                'source_identity_hash': source_identity_hash(user_locator),
            },
            ('master_learning_resources', 'master-resource-1'): {
                'id': 'master-resource-1', 'source_origin': 'POSTGRESQL', 'is_active': True,
                'source_identity_version': resource_locator.version, 'source_backend': resource_locator.backend,
                'source_namespace': resource_locator.namespace, 'source_entity_type': resource_locator.entity_type,
                'source_record_key': resource_locator.source_record_id,
                'source_identity_hash': source_identity_hash(resource_locator),
            },
        }
        self.jobs = {}

    def get(self, table, key, *, lock=False):
        return self.rows.get((table, key)) or self.jobs.get(key)

    def find(self, table, filters=None, *, order=(), limit=None, lock=False, skip_locked=False):
        filters = filters or {}
        rows = [row for row in self.jobs.values()
                if all(row.get(key) == value for key, value in filters.items())]
        return rows[:limit] if limit else rows

    def insert(self, table, values):
        row = dict(values)
        self.jobs[row['id']] = row
        return row

    def update(self, table, key, values):
        self.jobs[key].update(values)


class _Uow:
    def __init__(self):
        self.repo = _Repo()

    def __call__(self):
        outer = self

        class Context:
            def __enter__(self):
                return outer.repo

            def __exit__(self, *args):
                return False

        return Context()


class _VersionWriter:
    supported = True
    create_supported = True

    def __init__(self):
        self.ids = []
        self.calls = []
        self.fail_once = False

    def allows_create(self, **kwargs):
        return True

    def reserve_version_id(self, *, created_at):
        self.ids.append(f'RSV26010000{len(self.ids) + 1:02d}')
        return self.ids[-1]

    def create_version(self, **kwargs):
        self.calls.append(kwargs['source_version_id'])
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError('uncertain SOURCE commit')
        return {'status': 'APPLIED', 'source_state_hash': 'c' * 64,
                'idempotent_replay': len(self.calls) > 1}


class _ObjectWriter:
    supported = True

    def allows(self, **kwargs):
        return True

    def upload(self, stream, **kwargs):
        payload = stream.read()
        return {'status': 'OBJECT_VERIFIED', 'idempotent_replay': False,
                'object': CloudObject('source-cloud-current', 'source-uploads',
                                      'learning-resources/RES260100001/object', len(payload),
                                      __import__('hashlib').sha256(payload).hexdigest(),
                                      kwargs['content_type'])}


def test_source_version_job_retries_same_id_after_uncertain_commit():
    uow = _Uow()
    settings = SimpleNamespace(
        source_metadata_writeback_enabled=True,
        source_metadata_writeback_max_attempts=3,
        reconciliation_lease_seconds=60,
    )
    writer = _VersionWriter()
    service = SourceVersionCreateService(uow, writer, _ObjectWriter(), settings)
    pending = service.request(
        'master-resource-1', file_stream=io.BytesIO(b'%PDF-source'), filename='lesson.pdf',
        content_type='application/pdf', file_size=11, idempotency_key='version-upload-1',
        actor_id='master:master-user-1', role='ADMIN')
    assert pending['status'] == 'SOURCE_VERSION_PENDING'
    writer.fail_once = True
    assert service.process_pending_once('source-version-worker') is True
    row = next(iter(uow.repo.jobs.values()))
    assert row['status'] == 'SOURCE_VERSION_PENDING'
    assert row['checkpoint']['source_version_id'] == 'RSV2601000001'
    assert service.process_pending_once('source-version-worker') is True
    assert row['status'] == 'SOURCE_VERSION_APPLIED'
    assert writer.calls == ['RSV2601000001', 'RSV2601000001']


def test_source_create_file_workflow_checkpoints_object_and_version_before_cleanup(tmp_path):
    payload = b'%PDF-source-create'
    stager = SourceCreateStager(tmp_path / 'source-create')
    manifest = stager.stage(
        io.BytesIO(payload), idempotency_key='create-file-01', filename='lesson.pdf',
        content_type='application/pdf', file_size=len(payload))
    uow = _Uow()
    settings = SimpleNamespace(
        source_metadata_writeback_enabled=True,
        source_metadata_writeback_max_attempts=3,
        reconciliation_lease_seconds=60,
    )
    writer = _VersionWriter()
    service = SourceVersionCreateService(uow, writer, _ObjectWriter(), settings)
    checkpoint = {
        'source_resource_id': 'RES260100001',
        'source_user_id': 'USR260100001',
        'source_file': manifest,
        'source_version_idempotency_key': 'srcv-create-file-01',
    }
    saved = []

    def persist(updated):
        saved.append(dict(updated))

    writer.fail_once = True
    with pytest.raises(RuntimeError, match='uncertain SOURCE commit'):
        service.apply_for_source_resource(checkpoint=checkpoint, stager=stager,
                                          persist_checkpoint=persist)
    assert checkpoint['source_bucket'] == 'source-uploads'
    assert checkpoint['source_version_id'] == 'RSV2601000001'
    assert manifest['path'] and (tmp_path / 'source-create').joinpath(
        __import__('pathlib').Path(manifest['path']).name).exists()
    result = service.apply_for_source_resource(checkpoint=checkpoint, stager=stager,
                                               persist_checkpoint=persist)
    assert result['status'] == 'APPLIED'
    assert checkpoint['workflow'] == 'SOURCE_VERSION_COMMITTED'
    assert not __import__('pathlib').Path(manifest['path']).exists()
    assert writer.calls == ['RSV2601000001', 'RSV2601000001']
