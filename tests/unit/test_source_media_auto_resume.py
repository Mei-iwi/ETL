"""Isolated source media queue upgrade; no real ASR, remote DB or model."""
from hashlib import sha256

import pytest

from data_sync_etl.domain.core import now
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from tests.unit.test_metadata_media_registration import media_payload
from tests.unit.test_source_file_import import importing as importing


@pytest.fixture
def metadata_media(importing):
    c, rows, path = importing
    c.settings.source_metadata_media_enabled = True
    media = path.with_suffix('.mp3')
    payload = media_payload('.mp3')
    media.write_bytes(payload)
    rows[0].update(storage_object_key=media.name, source_hash=sha256(payload).hexdigest())
    c.source_file_import.formats = c.source_resolver.formats = FormatValidatorRegistry(media_registration=True)
    c.source_file_worker.once('metadata-fixture')
    with c.uow() as repo:
        row, = repo.find('source_file_imports')
        assert row['status'] == 'METADATA_ONLY' and row['processing_job_id'] is None
    c.settings.source_metadata_media_enabled = False
    c.source_file_import.formats = c.source_resolver.formats = FormatValidatorRegistry(media_processing=True)
    return c, rows, row, media


def test_worker_never_resumes_external_source_content(metadata_media):
    c, _, before, media = metadata_media
    original = media.read_bytes()
    assert c.source_file_worker.once('full-processing-fixture') is False
    with c.uow() as repo:
        after = repo.get('source_file_imports', before['id'])
        assert after['status'] == 'METADATA_ONLY' and after['processing_job_id'] is None
        assert after['target_version_id'] == before['target_version_id']
        assert after['attempts'] == before['attempts']
        assert after['manual_retries'] == before['manual_retries']
        assert repo.count('ocr_jobs', {}) == 0 and repo.count('resource_versions', {}) == 1
    assert c.source_file_worker.once('full-processing-fixture') is False
    assert media.read_bytes() == original
    assert not list((c.storage.root / 'resources').rglob('*.mp3'))


@pytest.mark.parametrize('blocked', ['metadata_policy', 'source_disabled', 'temporary_disabled',
                                    'source_inactive', 'source_rejected', 'master_deleted',
                                    'target_rejected', 'source_mutated'])
def test_policy_or_changed_source_never_auto_queues(metadata_media, blocked):
    c, rows, before, _ = metadata_media
    if blocked == 'metadata_policy':
        c.settings.source_metadata_media_enabled = True
    elif blocked == 'source_disabled':
        c.settings.source_reference_enabled = False
    elif blocked == 'temporary_disabled':
        c.settings.source_temporary_files_enabled = False
    elif blocked == 'source_inactive':
        rows[0]['resource_active'] = False
    elif blocked == 'source_rejected':
        rows[0]['source_lifecycle_status'] = 'REJECTED'
    elif blocked == 'source_mutated':
        rows[0]['source_hash'] = 'a' * 64
    else:
        with c.uow() as repo:
            if blocked == 'master_deleted':
                repo.update('master_learning_resources', before['resource_id'], {'master_deleted_at': now()})
            else:
                repo.update('resource_versions', before['target_version_id'], {'lifecycle_status': 'REJECTED'})
    with c.uow() as repo:
        c.source_file_import._discover(repo, rows[0])
    with c.uow() as repo:
        after = repo.get('source_file_imports', before['id'])
        assert after['status'] == ('FAILED' if blocked == 'source_mutated' else 'METADATA_ONLY')
        assert after['processing_job_id'] is None
        assert after['attempts'] == before['attempts']
        assert repo.count('ocr_jobs', {}) == 0


def test_external_source_file_changes_are_not_read_or_ocr_queued(metadata_media):
    c, _, before, media = metadata_media
    media.write_bytes(media.read_bytes() + b'changed')
    c.source_file_worker.once('hash-verification-fixture')
    with c.uow() as repo:
        after = repo.get('source_file_imports', before['id'])
        assert after['status'] == 'METADATA_ONLY'
        assert after['target_version_id'] == before['target_version_id']
        assert repo.count('ocr_jobs', {}) == 0


def test_worker_startup_does_not_resume_external_file_processing(metadata_media, monkeypatch):
    c, rows, _, _ = metadata_media
    c.settings.sync_batch_size = 1
    calls = []
    def page(after=None, size=20):
        calls.append((after, size))
        return {'items': [rows[0]] if after is None else [],
                'next_cursor': rows[0]['source_version_id'] if after is None else None}
    monkeypatch.setattr(c.source, 'version_page', page)
    assert c.source_file_worker.once('paged-worker') is False
    assert c.source_file_worker.once('paged-worker') is False
    assert c.source_file_worker.once('paged-worker') is False
    assert calls == []


def test_manual_job_is_linked_instead_of_created_again(metadata_media):
    c, _, before, _ = metadata_media
    with pytest.raises(Exception, match='CONTENT_NOT_AVAILABLE'):
        c.process_version.run(before['target_version_id'])
    assert c.source_file_worker.once('automatic-worker') is False
    with c.uow() as repo:
        row = repo.get('source_file_imports', before['id'])
        assert row['processing_job_id'] is None
        assert repo.count('ocr_jobs', {}) == 0
