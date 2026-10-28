import hashlib
import os
from pathlib import Path

import pytest

from data_sync_etl.db.models import ResourceVersion
from data_sync_etl.domain.core import new_id
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_reference import source_viewer as source_viewer


@pytest.fixture
def local_file(container, tmp_path):
    root = tmp_path / 'blob'
    root.mkdir()
    path = root / 'file.pdf'
    path.write_bytes(b'original')
    with container.uow() as repo:
        resource = repo.insert('master_learning_resources', {'id': new_id(), 'source_origin': 'LOCAL',
            'resource_type_id': 'type', 'resource_type_code': 'DOC', 'resource_type_name_vi': 'Document',
            'resource_type_is_active': True, 'publication_status': 'PUBLISHED', 'is_active': True})
        version = ResourceVersion(id=new_id(), resource_id=resource['id'], version_number=1,
            lifecycle_status='APPROVED', storage_bucket='', storage_object_key=path.name,
            source_hash=hashlib.sha256(b'original').hexdigest(), source_file_size=8)
        repo.session.add(version)
        repo.session.flush()
        version_id = version.id
    container.reconciliation_engine.storage_root = root
    return container, path, version_id


def test_light_never_opens_local_bytes_and_does_not_claim_sha256(local_file, monkeypatch):
    c, path, version_id = local_file
    def forbidden(*args, **kwargs):
        raise AssertionError('LIGHT read bytes')
    monkeypatch.setattr(Path, 'open', forbidden)
    with c.uow() as repo:
        rows = c.reconciliation_engine.verify_file_blobs(repo.session, audit_type='LIGHT')
    assert rows[0]['discrepancy_flag'] == 'NOT_VERIFIED'
    assert rows[0]['target_value']['size_status'] == 'SIZE_MATCHED'
    assert c.reconciliation_engine.file_metrics['bytes_read'] == 0


def test_deep_rereads_same_size_same_mtime_corruption(local_file):
    c, path, version_id = local_file
    with c.uow() as repo:
        assert c.reconciliation_engine.verify_file_blobs(repo.session)[0]['discrepancy_flag'] == 'SYNCED'
        stamp = path.stat()
        path.write_bytes(b'corrupt!')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        row = c.reconciliation_engine.verify_file_blobs(repo.session)[0]
    assert row['discrepancy_flag'] == 'DESYNC_CHECKSUM'
    assert c.reconciliation_engine.file_metrics == {'bytes_read': 8, 'sha256_computed_count': 1, 'cache_reused_count': 0}


def test_deep_byte_and_time_limits_are_not_verified(local_file):
    c, path, _ = local_file
    c.reconciliation_engine.file_max_bytes = 7
    with c.uow() as repo:
        assert c.reconciliation_engine.verify_file_blobs(repo.session)[0]['target_value']['reason_code'] == 'RECONCILIATION_BYTE_BUDGET'
        assert c.reconciliation_engine.file_metrics['bytes_read'] == 0
        c.reconciliation_engine.file_max_bytes = 100
        c.reconciliation_engine.file_read_budget_seconds = 0
        assert c.reconciliation_engine.verify_file_blobs(repo.session)[0]['target_value']['reason_code'] == 'RECONCILIATION_READ_BUDGET'


def test_light_source_uses_provider_revision_without_download(source_viewer, monkeypatch):
    c, version, source, path = source_viewer
    def forbidden(*args, **kwargs):
        raise AssertionError('LIGHT called download resolver')
    monkeypatch.setattr(c.source_resolver, 'temporary_path', forbidden)
    with c.uow() as repo:
        row = c.reconciliation_engine.verify_file_blobs(repo.session, audit_type='LIGHT')[0]
    assert row['discrepancy_flag'] == 'NOT_VERIFIED'
    assert row['target_value']['verification'] == 'SIZE_ONLY'
    assert c.reconciliation_engine.file_metrics['bytes_read'] == 0


def test_drive_audit_budget_counts_eager_download_and_hash_stream():
    from data_sync_etl.adapters.persistence.audit_io import MeteredFiles
    from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
    from data_sync_etl.domain.core import SourceFileError
    from tests.unit.test_source_google_drive import DriveHTTP, reader
    class HTTP(DriveHTTP):
        def get(self, *args, **kwargs):
            response = super().get(*args, **kwargs)
            if kwargs['params'].get('alt') == 'media':
                response.iter_content = lambda size: (response.payload[i:i + size]
                    for i in range(0, len(response.payload), size))
            return response
    http = HTTP(payload=b'content')
    original = reader(http)
    dispatcher = SourceFileDispatcher(None, original)
    metrics = {'bytes_read': 0}
    with MeteredFiles(dispatcher, metrics, 14, 'google_drive').open_stream('DRIVE', 'files/blob/sample.txt') as stream:
        assert stream.read() == b'content'
        assert stream.read() == b''
    assert metrics['bytes_read'] == 14  # Seven download + seven hash-input bytes.
    http.calls.clear()
    metrics = {'bytes_read': 0}
    with pytest.raises(SourceFileError, match='RECONCILIATION_BYTE_BUDGET'):
        with MeteredFiles(dispatcher, metrics, 13, 'google_drive').open_stream('DRIVE', 'files/blob/sample.txt'):
            raise AssertionError('Download must not start')
    assert not any(params.get('alt') == 'media' for _, params in http.calls)
    assert metrics['bytes_read'] == 0
    with original.open_stream('DRIVE', 'files/blob/sample.txt') as stream:
        assert stream.read() == b'content'  # Audit policy never mutates the sync reader.


def test_deep_then_unchanged_light_preserves_proof_but_not_light_hash_credit(local_file):
    from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
    from data_sync_etl.db.models import ReconciliationFileState as State, ReconciliationAuditJob as Job
    from data_sync_etl.main import create_app
    from tests.local_admin_client import TestClient

    c, _, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.once(audit.queue.enqueue('DEEP', 'files', mode='FULL'))
    with c.uow() as repo:
        stamp = repo.session.get(State, version_id).last_verified_at
    light = audit.queue.enqueue('LIGHT', 'files')
    audit.once(light)
    c.settings.admin_enabled = True
    with TestClient(create_app(c)) as client:
        coverage = client.get('/api/v1/reconciliation/coverage').json()
        assert coverage['recent_sha256_evidence_count'] == coverage['total_files'] == 1
        assert coverage['last_deep_verified_at'] is not None
        evidence = client.get('/api/v1/reconciliation/file-evidence/' + version_id).json()
        assert evidence['status'] == 'SYNCED' and evidence['recent_sha256_evidence'] is True
        latest = client.get('/api/v1/reconciliation/runs/latest?stream=files').json()
        assert latest['stream_reports'][0]['summary_meta']['audit_type'] == 'LIGHT'
    with c.uow() as repo:
        assert repo.session.get(State, version_id).last_verified_at == stamp
        job = repo.session.get(Job, light)
        assert job.checkpoint['bytes_read'] == job.checkpoint['sha256_computed_count'] == 0
        assert job.checkpoint['matched_count'] == 0


@pytest.mark.parametrize('change', ['revision', 'expected_hash', 'expected_size', 'ownership', 'missing_revision'])
def test_light_invalidates_changed_or_unprovable_binding(local_file, monkeypatch, change):
    from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
    from data_sync_etl.adapters.persistence.audit_queue import utc
    from data_sync_etl.db.models import ReconciliationFileState as State
    from data_sync_etl.domain.core import now

    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.once(audit.queue.enqueue('DEEP', 'files', mode='FULL'))
    assert audit.coverage()['recent_sha256_evidence_count'] == 1
    if change == 'revision':
        path.write_bytes(b'corrupt!')
    elif change == 'missing_revision':
        original = type(c.reconciliation_engine).verify_file_snapshot
        def without_revision(self, *args, **kwargs):
            rows = original(self, *args, **kwargs)
            rows[0]['target_value'].pop('observed_revision', None)
            return rows
        monkeypatch.setattr(type(c.reconciliation_engine), 'verify_file_snapshot', without_revision)
    else:
        with c.uow() as repo:
            version = repo.session.get(ResourceVersion, version_id)
            if change == 'expected_hash':
                version.source_hash = 'f' * 64
            elif change == 'expected_size':
                version.source_file_size = 9
            else:
                resource = repo.get('master_learning_resources', version.resource_id)
                repo.insert('master_learning_resources', {**resource, 'id': 'new-owner'})
                version.resource_id = 'new-owner'
    audit.once(audit.queue.enqueue('LIGHT', 'files'))
    assert audit.coverage()['recent_sha256_evidence_count'] == 0
    with c.uow() as repo:
        state = repo.session.get(State, version_id)
        assert state.verification_method == 'STALE_SHA256'
        assert state.last_verified_hash is not None
        assert utc(state.next_due_at) <= now()
    audit.once(audit.queue.enqueue('LIGHT', 'files'))
    assert audit.coverage()['recent_sha256_evidence_count'] == 0


@pytest.mark.parametrize('change', ['checksum', 'missing', 'budget', 'invalid_hash'])
def test_failed_deep_followed_by_light_does_not_revive_old_proof(local_file, change):
    from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit

    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.once(audit.queue.enqueue('DEEP', 'files', mode='FULL'))
    if change == 'checksum':
        path.write_bytes(b'corrupt!')
    elif change == 'missing':
        path.unlink()
    elif change == 'budget':
        c.settings.reconciliation_max_bytes_per_run = 7
    else:
        with c.uow() as repo:
            repo.session.get(ResourceVersion, version_id).source_hash = 'invalid'
    audit.once(audit.queue.enqueue('DEEP', 'files', mode='FULL'))
    c.settings.reconciliation_max_bytes_per_run = 100
    audit.once(audit.queue.enqueue('LIGHT', 'files'))
    assert audit.coverage()['recent_sha256_evidence_count'] == 0
    assert audit.coverage()['not_verified_count'] == 1


def test_light_without_deep_and_partial_deep_report_actual_repository_coverage(local_file):
    from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit

    c, _, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.once(audit.queue.enqueue('LIGHT', 'files'))
    assert audit.coverage()['recent_sha256_evidence_count'] == 0
    with c.uow() as repo:
        values = repo.get('resource_versions', version_id)
        repo.session.add(ResourceVersion(**{**values, 'id': new_id(), 'version_number': 2}))
    c.settings.reconciliation_max_files_per_run = 1
    audit.once(audit.queue.enqueue('DEEP', 'files', mode='FULL'))
    coverage = audit.coverage()
    assert coverage['recent_sha256_evidence_count'] == 1
    assert coverage['total_files'] == 2 and coverage['not_verified_count'] == 1
    assert coverage['coverage_status'] == 'COVERAGE_BEHIND'
