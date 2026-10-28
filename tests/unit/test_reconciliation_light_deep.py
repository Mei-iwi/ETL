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
