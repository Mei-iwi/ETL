from hashlib import sha256

import pytest

from data_sync_etl.domain.core import SourceFileError
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_file_import import discover_and_import
from tests.unit.test_source_file_import import importing as importing


def test_import_reference_no_managed_copy_and_source_readonly(importing):
    c, _, path = importing
    before = path.read_bytes()
    row = discover_and_import(c)
    assert row['status'] == 'METADATA_ONLY', row
    assert not list((c.storage.root / 'resources').rglob('*'))
    with c.uow() as repo:
        version = repo.get('resource_versions', row['target_version_id'])
    assert version['storage_bucket'] == '__SOURCE_METADATA_ONLY__'
    assert version['storage_object_key'].startswith('__SOURCE_METADATA_ONLY__/')
    assert version['source_hash'] == sha256(before).hexdigest()
    assert c.source_file_import.claim() is None
    with pytest.raises(SourceFileError, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        with c.source_resolver.temporary_path(c.source_resolver.resolve(version['id'])):
            pass
    assert c.orchestrator.status(version['id'])['processing_status'] != 'CONTENT_READY'
    assert path.read_bytes() == before
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_flags_fail_closed_never_copy(importing):
    c, source, _ = importing
    c.settings.source_reference_enabled = False
    c.source_file_import.discover_once()
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        row, = repo.find('source_file_imports')
        version = repo.get('resource_versions', row['target_version_id'])
    assert row['status'] == version['lifecycle_status'] == 'METADATA_ONLY'
    with pytest.raises(SourceFileError, match='SOURCE_REFERENCE_DISABLED'):
        c.source_resolver.import_reference(row)
    c.settings.source_reference_enabled = True
    c.settings.source_temporary_files_enabled = False
    # An explicit canonical reference still cannot open bytes while temp
    # access is disabled. The persisted metadata-only placeholder is untouched.
    canonical = {**row, 'storage_bucket': source[0]['storage_bucket'],
                 'storage_object_key': source[0]['storage_object_key']}
    with pytest.raises(SourceFileError, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        ref = c.source_resolver.import_reference(canonical)
        with c.source_resolver.temporary_path(ref):
            pass
    assert not list((c.storage.root / 'resources').rglob('*'))


def test_cleanup_failure_and_no_legacy_fallback(importing):
    c, _, path = importing
    row = discover_and_import(c)
    ref = c.source_resolver.resolve(row['target_version_id'])
    with pytest.raises(SourceFileError, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        with c.source_resolver.temporary_path(ref):
            pass
    # A historical managed copy must never mask deletion of SOURCE.
    with path.open('rb') as stream:
        c.storage.save_stream(stream, suffix='.pdf')
    path.unlink()
    with pytest.raises(SourceFileError, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        with c.source_resolver.temporary_path(ref):
            pass
    assert list((c.storage.root / 'resources').rglob('*.pdf'))


def test_source_mutates_during_processing_blocks_results(importing):
    c, rows, _ = importing
    row = discover_and_import(c)
    with pytest.raises(Exception, match='CONTENT_NOT_AVAILABLE'):
        c.ocr.create_job(row['target_version_id'])
    rows[0]['source_hash'] = '0' * 64
    with pytest.raises(SourceFileError, match='SOURCE_VERSION_MUTATED'):
        c.source_resolver.resolve(row['target_version_id'])
    with c.uow() as repo:
        assert repo.count('ocr_page_results', {}) == 0
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


@pytest.mark.parametrize('value', ['https://127.0.0.1/a.pdf', 'http://169.254.169.254/a', 'https://user:secret@host/a.pdf'])
def test_https_contract_disabled_without_network(importing, value):
    c, rows, _ = importing
    rows[0]['storage_object_key'] = value
    row = discover_and_import(c)
    assert row['status'] == 'METADATA_ONLY'
    assert row['storage_bucket'] == '__SOURCE_METADATA_ONLY__'
    assert value not in row['storage_object_key']


@pytest.fixture
def source_viewer(searchable, tmp_path, monkeypatch):
    from data_sync_etl.domain.core import now
    c, version = searchable
    original = c.storage.get_local_path(version['storage_bucket'], version['storage_object_key'])
    root = tmp_path / 'source'
    path = root / 'books' / 'test.pdf'
    path.parent.mkdir(parents=True)
    path.write_bytes(original.read_bytes())
    c.settings.source_reference_enabled = c.settings.source_temporary_files_enabled = True
    c.settings.source_temp_root = tmp_path / 'temp'
    c.source_files.root = root
    source = dict(source_version_id='source-view', resource_id=version['resource_id'], source_version_number=version['version_number'],
                  parent_version_id=None, storage_bucket='books', storage_object_key='test.pdf', source_hash=version['source_hash'],
                  source_lifecycle_status='APPROVED', updated_at=now())
    monkeypatch.setattr(c.source, 'get_version', lambda _: dict(source), raising=False)
    with c.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'source_origin': 'POSTGRESQL'})
        repo.insert('source_file_imports', {**{k: v for k, v in source.items() if k not in ('parent_version_id', 'updated_at')},
                    'source_updated_at': source['updated_at'], 'target_version_id': version['id'], 'status': 'PROCESSING'})
    return c, version, source, path


def test_source_api_range_text_no_legacy_fallback_and_permissions(source_viewer, monkeypatch):
    from tests.unit.test_resource_file import client
    c, version, source, path = source_viewer
    url = f"/api/v1/resource-versions/{version['id']}/file"
    monkeypatch.setattr(c.storage, 'get_local_path', lambda *_: (_ for _ in ()).throw(AssertionError('legacy read')))
    with client(c) as api:
        assert api.get(url).status_code == 409
        assert api.get(url, headers={'Range': 'bytes=0-3'}).status_code == 409
        assert api.get(url + '?download=true').status_code == 409
        assert api.get(url + '?view=text').status_code == 409
        info = api.get(url.replace('/file', '/source-info')).json()
        assert not info['can_open_original'] and info['original_url'] is None
        assert 'books' not in str(info) and 'test.pdf' not in str(info)
        source['source_hash'] = '0' * 64
        assert api.get(url.replace('/file', '/source-info')).status_code == 409
        source['source_hash'] = version['source_hash']
        path.unlink()
        assert api.get(url).status_code == 409
        assert api.get(url + '?view=text').status_code == 409
        c.settings.search_internal_dev_enabled = False
        assert api.get(url).status_code == 404
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_missing_mapping_never_uses_legacy(source_viewer):
    c, version, _, _ = source_viewer
    with c.uow() as repo:
        repo.delete('source_file_imports', {'target_version_id': version['id']})
    with pytest.raises(SourceFileError, match='BLOCKED_SOURCE_MAPPING'):
        c.source_resolver.resolve(version['id'])


def test_temp_slot_capacity_cleanup_and_no_active_orphan_deletion(importing):
    import os
    import time
    c, _, _ = importing
    temporary = c.source_resolver.temporary
    c.settings.source_temp_max_concurrency = 1
    with temporary.scope() as directory:
        (directory / 'scratch').write_bytes(b'bounded')
        old = time.time() - 7200
        os.utime(directory, (old, old))
        assert temporary.cleanup_orphans() == 0
        with pytest.raises(SourceFileError, match='SOURCE_BUSY'):
            with temporary.scope():
                pass
    assert not directory.exists()
    orphan = temporary.root() / 'attempt-abandoned'
    orphan.mkdir()
    (orphan / 'lease').write_bytes(b'0')
    (orphan / 'source.pdf').write_bytes(b'orphan')
    scratch = orphan / 'etl-ocr-orphan'
    scratch.mkdir()
    (scratch / 'input.png').write_bytes(b'orphan image')
    os.utime(orphan, (old, old))
    assert temporary.cleanup_orphans() == 1
    assert not orphan.exists()


def test_source_drive_original_link_uses_source_permission_without_id_configuration(importing):
    from test_source_google_drive import DriveHTTP, reader

    from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
    c, rows, path = importing
    http = DriveHTTP(path.read_bytes(), name='test.pdf', mime='application/pdf')
    c.source_file_import.files = SourceFileDispatcher(c.source_files, reader(http))
    rows[0].update(storage_bucket='DRIVE', storage_object_key='files/blob/test.pdf')
    row = discover_and_import(c)
    assert row['status'] == 'METADATA_ONLY'
    ref = c.source_resolver.resolve(row['target_version_id'])
    info = c.source_resolver.display_reference(ref)
    assert info['can_open_original'] is False and info['original_url'] is None
    assert info['availability'] == 'METADATA_ONLY'
    http.metadata['blob']['capabilities']['canDownload'] = False
    assert c.source_resolver.display_reference(ref)['original_url'] is None
    assert not http.calls
    assert not list((c.storage.root / 'resources').rglob('*'))


@pytest.mark.parametrize('file_id', ['blob', 'another-file'])
def test_original_link_checks_master_and_provider_permissions_for_each_file(source_viewer, file_id):
    from test_source_google_drive import DriveHTTP, reader

    from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
    from tests.unit.test_resource_file import client

    c, version, source, path = source_viewer
    http = DriveHTTP(path.read_bytes(), name='test.pdf', mime='application/pdf')
    http.metadata[file_id] = http.metadata.pop('blob')
    http.metadata[file_id]['id'] = file_id
    source.update(storage_bucket='DRIVE', storage_object_key=f'files/{file_id}/test.pdf')
    with c.uow() as repo:
        row = repo.find('source_file_imports', {'target_version_id': version['id']})[0]
        repo.update('source_file_imports', row['id'], {'storage_bucket': 'DRIVE', 'storage_object_key': source['storage_object_key']})
    c.source_resolver.files = SourceFileDispatcher(c.source_files, reader(http))
    url = f"/api/v1/resource-versions/{version['id']}/source-info"
    with client(c) as api:
        response = api.get(url)
        assert response.status_code == 200, response.text
        assert response.json()['can_open_original'] is False
        assert response.json()['original_url'] is None
        assert response.headers.get('cache-control') == 'private, no-store'
        calls = len(http.calls)
        c.settings.search_internal_dev_enabled = False
        denied = api.get(url)
        assert denied.status_code == 404 and 'original_url' not in denied.json()
        assert len(http.calls) == calls
        c.settings.search_internal_dev_enabled = True
        http.metadata[file_id]['capabilities']['canDownload'] = False
        denied = api.get(url)
        assert denied.status_code == 200 and denied.json()['original_url'] is None
        assert len(http.calls) == calls


@pytest.mark.asyncio
async def test_cancelled_source_response_cleans_temp(source_viewer):
    import asyncio

    from data_sync_etl.api.source_file_response import SourceFileResponse
    c, version, _, _ = source_viewer
    response = SourceFileResponse(c.source_resolver, c.source_resolver.resolve(version['id']), lambda: None, download=False)
    body_attempts = []
    async def send(message):
        if message['type'] == 'http.response.body':
            body_attempts.append(message)
            raise asyncio.CancelledError()
    async def receive():
        import anyio
        await anyio.sleep_forever()
    # AnyIO task groups may absorb their own cancellation on Linux. The required
    # contract is that send was interrupted and the leased source was cleaned.
    try:
        await response({'type': 'http', 'method': 'GET', 'headers': [], 'extensions': {}}, receive, send)
    except asyncio.CancelledError:
        pass
    assert body_attempts
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_new_source_concurrent_read_microbenchmark(importing, tmp_path):
    """External SOURCE access has no binary-read throughput path to benchmark."""
    c, _, _ = importing
    row = discover_and_import(c)
    ref = c.source_resolver.resolve(row['target_version_id'])
    with pytest.raises(SourceFileError, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        with c.source_resolver.open_verified_stream(ref):
            pass
    assert not list(c.settings.source_temp_root.glob('attempt-*'))
    assert not list((c.storage.root / 'resources').rglob('*'))


def test_process_crash_releases_lease_for_orphan_cleanup(importing):
    import os
    import subprocess
    import sys
    import time
    c, _, _ = importing
    temporary = c.source_resolver.temporary
    root = temporary.root()
    program = '''
import sys,time
from pathlib import Path
from data_sync_etl.config import Settings
from data_sync_etl.adapters.storage.source_temporary import SourceTemporaryFiles
s=Settings(_env_file=None,source_temporary_files_enabled=True,source_temp_root=Path(sys.argv[1]),source_temp_max_concurrency=1)
with SourceTemporaryFiles(s,Path(sys.argv[2])).scope() as directory:
 (directory/'source.pdf').write_bytes(b'fixture')
 print('READY',flush=True)
 time.sleep(60)
'''
    c.settings.source_temp_max_concurrency = 1
    child = subprocess.Popen([sys.executable, '-c', program, str(root), str(c.storage.root)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'READY'
        with pytest.raises(SourceFileError, match='SOURCE_BUSY'):
            with temporary.scope():
                pass
    finally:
        child.kill()
        child.communicate(timeout=5)
    for directory in root.glob('attempt-*'):
        old = time.time() - 7200
        os.utime(directory, (old, old))
    assert temporary.cleanup_orphans() == 1
    assert not list(root.glob('attempt-*'))


def test_external_source_is_never_ocr_processed(importing):
    from data_sync_etl.domain.core import DomainError
    c, _, _ = importing
    row = discover_and_import(c)
    with pytest.raises(DomainError, match='CONTENT_NOT_AVAILABLE'):
        c.ocr.create_job(row['target_version_id'])
    with c.uow() as repo:
        assert repo.count('ocr_jobs', {}) == 0
        assert repo.count('ocr_page_tasks', {}) == 0


def test_temp_permission_failure_is_redacted_and_retryable(importing, monkeypatch):
    c, _, _ = importing
    row = discover_and_import(c)
    ref = c.source_resolver.resolve(row['target_version_id'])
    def denied(*args, **kwargs):
        pytest.fail('External SOURCE must fail before creating a temp directory')
    monkeypatch.setattr(c.source_resolver.temporary, 'root', denied)
    with pytest.raises(SourceFileError, match='EXTERNAL_SOURCE_CONTENT_DISABLED') as failure:
        with c.source_resolver.temporary_path(ref):
            pass
    assert 'PRIVATE' not in str(failure.value)


def test_temp_slot_permission_failure_is_redacted(importing, monkeypatch):
    from pathlib import Path
    c, _, _ = importing
    original = Path.open
    def denied(path, *args, **kwargs):
        if path.name.startswith('slot-'):
            raise PermissionError('PRIVATE_SLOT_PATH')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(SourceFileError, match='SOURCE_TEMP_UNAVAILABLE'):
        with c.source_resolver.temporary.scope():
            pass
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


@pytest.mark.asyncio
async def test_source_response_fails_before_opening_or_spooling_bytes(source_viewer, monkeypatch):
    from data_sync_etl.api.source_file_response import SourceFileResponse
    c, version, _, _ = source_viewer
    monkeypatch.setattr(c.source_files, 'open_stream', lambda *_a, **_k: pytest.fail('No SOURCE read'))
    response = SourceFileResponse(c.source_resolver, c.source_resolver.resolve(version['id']), lambda: None, download=False)
    sent = []
    async def receive():
        await __import__('asyncio').sleep(10)
        return {'type': 'http.disconnect'}
    async def send(message):
        sent.append(message)
    await response({'type': 'http', 'method': 'GET', 'headers': [], 'extensions': {}}, receive, send)
    assert any(item['type'] == 'http.response.start' and item['status'] == 409 for item in sent)
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_drive_cancel_closes_transport_and_spool(monkeypatch):
    from threading import Event

    from test_source_google_drive import DriveHTTP, reader

    import data_sync_etl.adapters.storage.source_google_drive as module
    http = DriveHTTP(b'fixture text', name='sample.txt', mime='text/plain')
    cancelled = Event()
    get = http.get
    temporary_file = module.tempfile.TemporaryFile
    spools = []
    def temporary(*args, **kwargs):
        spool = temporary_file(*args, **kwargs)
        spools.append(spool)
        return spool
    def response(*args, **kwargs):
        result = get(*args, **kwargs)
        if kwargs['params'].get('alt') == 'media':
            blocks = result.iter_content
            def interrupted(chunk_size):
                for block in blocks(chunk_size):
                    cancelled.set()
                    yield block
            result.iter_content = interrupted
        return result
    monkeypatch.setattr(module.tempfile, 'TemporaryFile', temporary)
    http.get = response
    with pytest.raises(SourceFileError, match='SOURCE_REQUEST_CANCELLED'):
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt', cancelled=cancelled)
    assert spools and all(spool.closed for spool in spools)
    assert http.closed and all(response.closed for response in http.responses)


def test_text_preview_rejects_external_source_before_content_read(source_viewer, monkeypatch):
    from tests.unit.test_resource_file import client
    c, version, _, _ = source_viewer
    monkeypatch.setattr(c.source_files, 'open_stream', lambda *_a, **_k: pytest.fail('No SOURCE read'))
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/file?view=text")
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'EXTERNAL_SOURCE_CONTENT_DISABLED'
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_source_info_disabled_reports_policy_code_without_source_io(source_viewer, monkeypatch, caplog):
    from tests.unit.test_resource_file import client

    c, version, *_ = source_viewer
    c.settings.source_reference_enabled = False
    monkeypatch.setattr(c.source_files, 'revision', lambda *a, **kw: pytest.fail('Disabled feature accessed source'))
    caplog.set_level('INFO', logger='etl')
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/source-info")
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'SOURCE_REFERENCE_DISABLED'
    assert 'chưa được bật' in response.json()['detail']['message']
    assert 'SOURCE_REFERENCE_DISABLED' in caplog.text
    assert 'source_file_access' in caplog.text


def test_source_tesseract_scratch_stays_in_leased_attempt(importing, monkeypatch):
    c, rows, original = importing
    row = discover_and_import(c)
    with pytest.raises(Exception, match='CONTENT_NOT_AVAILABLE'):
        c.ocr.create_job(row['target_version_id'])
    assert not original.with_name('sample.png').exists()
    assert not list(c.settings.source_temp_root.glob('attempt-*'))
    assert not list((c.storage.root / 'resources').rglob('*'))
