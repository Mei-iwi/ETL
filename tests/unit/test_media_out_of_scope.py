"""Media scope regression: corrupt uploads rejected, legacy content preserved.

The former blanket MP3/MP4 rejection is superseded by content validation.
Real valid-media acceptance is covered in integration/test_media_local.py.
"""

import io
from hashlib import sha256

import pytest

from data_sync_etl.domain.core import now
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.main import create_app
from tests.indexing_support import setup_index
from tests.local_admin_client import TestClient


@pytest.fixture(autouse=True)
def local_owned_media_fixture(container):
    # This module tests format scope, not SOURCE ownership; use a real LOCAL target.
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update("master_learning_resources", "resource-1", {"source_origin": "LOCAL"})
    setup_index(container)

MEDIA = [('.mp3', b'ID3old-media', 'audio/mpeg'),
         ('.mp4', b'\x00\x00\x00\x18ftypold-media', 'video/mp4')]


@pytest.mark.parametrize('extension,payload,mime', MEDIA)
def test_upload_and_new_version_reject_media_without_writes(container, extension, payload, mime):
    container.settings.admin_enabled = True
    container.sync.run(full=True)
    with container.uow() as repo:
        before = {table: repo.count(table, {}) for table in ('master_learning_resources', 'resource_versions', 'ocr_jobs')}
    before_files = set(container.storage.root.rglob('*'))
    with TestClient(create_app(container), client=('127.0.0.1', 50000)) as client:
        response = client.post('/api/v1/resources', data={
            'title': 'Rejected media', 'resource_type_code': 'HANDOUT',
            'provider_id': 'provider-test', 'provider_name': 'Test provider',
        }, files={'file': ('sample' + extension, payload, mime)})
        assert response.status_code == 400, response.text
        assert 'Unsupported file extension' not in response.text
        version = client.post('/api/v1/resource/resource-1/versions',
                              files={'file': ('sample' + extension, payload, mime)})
        assert version.status_code == 400, version.text
        assert 'Unsupported file extension' not in version.text
    with container.uow() as repo:
        assert before == {table: repo.count(table, {}) for table in before}
    assert before_files == set(container.storage.root.rglob('*'))


@pytest.mark.parametrize('extension,payload,mime', MEDIA)
@pytest.mark.parametrize('existing_job', [False, True])
def test_corrupt_old_media_fails_safely_without_replacing_original(container, pdf_file, extension, payload, mime, existing_job):
    container.settings.admin_enabled = True
    container.sync.run(full=True)
    with TestClient(create_app(container), client=('127.0.0.1', 50000)) as client:
        response = client.post('/api/v1/resource/resource-1/versions',
                               files={'file': ('sample.pdf', pdf_file.read_bytes(), 'application/pdf')})
        version_id = response.json()['resource_version_id']
        job_id = container.process_version.run(version_id)['job_id'] if existing_job else None
        stored = container.storage.save_stream(io.BytesIO(payload), suffix=extension)
        with container.uow() as repo:
            repo.update('resource_versions', version_id, {
                'storage_bucket': stored.bucket, 'storage_object_key': stored.object_key,
                'source_hash': stored.source_hash,
            })
            before = repo.get('resource_versions', version_id)
        started = client.post(f'/api/v1/resource-versions/{version_id}/process')
        assert started.status_code == 202
        job_id = started.json()['job_id']
        assert client.post(f'/api/v1/processing-jobs/{job_id}/retry').status_code == 200
        for _ in range(30):
            if not container.worker.once('old-media'):
                break
        status = client.get(f'/api/v1/processing-jobs/{job_id}')
        assert status.status_code == 200 and status.json()['status'] == 'COMPLETED_WITH_ERRORS'
        assert status.json()['completed_pages'] == 0
        assert status.json()['failed_pages'] == status.json()['total_pages']
        assert status.json()['error_code'] in ('MEDIA_RUNTIME_UNAVAILABLE', 'INVALID_MEDIA')
        with container.uow() as repo:
            assert repo.count('ocr_page_results', {}) == 0
            assert all(t['status'] == 'FAILED' and 1 <= t['retry_count'] <= container.ocr.max_retries
                       for t in repo.find('ocr_page_tasks', {'job_id': job_id}))
        assert container.ocr.claim('old-media') is None
        versions = client.get('/api/v1/resources/resource-1/versions')
        assert versions.status_code == 200
        assert any(item['id'] == version_id for item in versions.json())
    with container.uow() as repo:
        current = repo.get('resource_versions', version_id)
        assert all(current[key] == before[key] for key in ('source_hash', 'storage_bucket', 'storage_object_key'))
        assert repo.count('ocr_jobs', {}) == 1
        assert repo.count('etl_stage_runs', {'stage': 'OCR_RETRY'}) == 0
    with container.storage.open_stream(stored.bucket, stored.object_key) as stream:
        assert stream.read() == payload


@pytest.mark.parametrize('extension,payload,mime', MEDIA)
def test_completed_old_media_content_stays_readable(container, pdf_file, extension, payload, mime):
    container.settings.admin_enabled = True
    container.sync.run(full=True)
    with TestClient(create_app(container), client=('127.0.0.1', 50000)) as client:
        created = client.post('/api/v1/resource/resource-1/versions',
                              files={'file': ('sample.pdf', pdf_file.read_bytes(), 'application/pdf')}).json()
        version_id = created['resource_version_id']
        job_id = container.process_version.run(version_id)['job_id']
        for _ in range(10):
            if not container.worker.once('content-test'):
                break
        before = client.get(f'/api/v1/resource-versions/{version_id}/content-units').json()
        assert before['items']
        stored = container.storage.save_stream(io.BytesIO(payload), suffix=extension)
        with container.uow() as repo:
            repo.update('resource_versions', version_id, {
                'storage_bucket': stored.bucket, 'storage_object_key': stored.object_key,
            })
        assert client.post(f'/api/v1/processing-jobs/{job_id}/retry').status_code == 200
        status = client.get(f'/api/v1/processing-jobs/{job_id}').json()
        assert status['processing_status'] == 'CONTENT_READY'
        assert status['error_code'] is None
        assert client.get(f'/api/v1/resource-versions/{version_id}/content-units').json() == before


@pytest.mark.parametrize('extension,payload,mime', MEDIA)
def test_source_media_missing_file_is_not_misreported_as_unsupported(container, monkeypatch, tmp_path, extension, payload, mime):
    root = tmp_path / 'source'
    root.mkdir()
    container.source_files.root = root
    container.sync.run(full=True)
    rows = [{'source_version_id': 'excluded-media', 'resource_id': 'resource-1',
             'resource_title': 'Excluded', 'source_version_number': 1,
             'source_lifecycle_status': 'APPROVED', 'updated_at': now(),
             'source_hash': sha256(payload).hexdigest(), 'storage_bucket': 'books',
             'storage_object_key': 'sample' + extension}]
    monkeypatch.setattr(container.source, 'version_page', lambda **_: {'items': rows, 'next_cursor': None}, raising=False)
    container.settings.source_reference_enabled = True
    container.settings.source_temporary_files_enabled = True
    container.settings.source_temp_root = tmp_path / 'scratch'
    # SOURCE original-file import is intentionally disabled.  Media metadata
    # may be inventoried, but the legacy worker must not read or queue bytes.
    assert not container.source_file_worker.once('excluded-media')
    items = container.source_file_import.list_files()['items']
    assert all(item['import_status'] == 'METADATA_ONLY' for item in items)
    with container.uow() as repo:
        assert repo.count('ocr_jobs', {}) == 0
        assert repo.count('resource_versions', {}) >= 1
        assert all(row['next_retry_at'] is None for row in repo.find('source_file_imports'))


def test_capabilities_exact_supported_formats(container):
    expected = {'.pdf', '.docx', '.xlsx', '.txt', '.csv', '.pptx', '.png', '.jpg', '.jpeg', '.mp3', '.mp4'}
    assert {item['extension'] for item in FormatValidatorRegistry().describe()} == expected
    with TestClient(create_app(container)) as client:
        response = client.get('/api/v1/system/capabilities')
    assert response.status_code == 200
    assert {item['extension'] for item in response.json()['formats']} == expected
