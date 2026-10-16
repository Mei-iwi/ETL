import json
from hashlib import sha256

import pytest
from test_multiformat import FORMATS, ImageEngineDouble, sample, xlsx_with_default_content_type
from test_source_file_import import importing as importing

from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
from data_sync_etl.adapters.storage.source_google_drive import (
    API,
    CHUNK,
    FOLDER,
    SourceGoogleDriveFileReader,
)
from data_sync_etl.adapters.storage.source_local import SourceLocalFileReader
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import SourceFileError
from data_sync_etl.domain.format_validation import FormatValidatorRegistry


class Response:
    def __init__(self, payload=b'', status=200, fault=None):
        self.payload, self.status_code, self.fault = payload, status, fault
        self.closed = False

    def iter_content(self, chunk_size):
        assert chunk_size == CHUNK
        if self.fault:
            yield self.payload[:3]
            raise self.fault
        for offset in range(0, len(self.payload), chunk_size):
            yield self.payload[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class DriveHTTP:
    def __init__(self, payload=b'content', name='sample.txt', mime='text/plain'):
        self.payload = payload
        self.metadata = {'blob': dict(id='blob', name=name, mimeType=mime, size=str(len(payload)),
                                     version='1', parents=['root'], trashed=False, capabilities={'canDownload': True}),
                         'root': dict(id='root', mimeType=FOLDER, trashed=False)}
        self.calls, self.responses, self.statuses = [], [], []
        self.after = None
        self.download_fault = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def get(self, url, *, params, stream, allow_redirects, timeout):
        assert url.startswith(API) and params['supportsAllDrives'] == 'true'
        assert stream is True and allow_redirects is False and 0 < timeout <= 15
        self.calls.append((url, params))
        if self.statuses:
            status = self.statuses.pop(0)
            if isinstance(status, Exception):
                raise status
            response = Response(b'PRIVATE_PROVIDER_ERROR', status=status)
        elif params.get('alt') == 'media':
            response = Response(self.payload, fault=self.download_fault)
            if self.after:
                self.metadata['blob'].update(self.after)
        else:
            response = Response(json.dumps(self.metadata[url.removeprefix(API)]).encode())
        self.responses.append(response)
        return response


def reader(http=None, **overrides):
    settings = Settings(_env_file=None, source_drive_enabled=True,
                        source_drive_buckets={'DRIVE': 'root'}, **overrides)
    return SourceGoogleDriveFileReader(settings, session_factory=lambda: http, sleep=lambda _: None)


def test_blob_disk_stream_closed_by_caller_and_no_secret_or_url_transport():
    http = DriveHTTP(payload=b'ab' * (CHUNK + 1))
    r = reader(http)
    with r.open_stream('DRIVE', 'files/blob/sample.txt') as stream:
        assert sha256(stream.read()).hexdigest() == sha256(http.payload).hexdigest()
    assert stream.closed and http.closed and all(response.closed for response in http.responses)
    assert all(call[0] in (API + 'blob', API + 'root') for call in http.calls)


@pytest.mark.parametrize('key', [
    'blob', 'https://drive.google.com/file/d/blob/view', 'files/blob/../sample.txt',
    'files//sample.txt', 'files/blob/sample.txt:secret', 'files/blob/%2e%2e.pdf',
    'files/blob/sample\\txt.pdf', 'files/blob/sample.txt\x00', 'files/blob/..', 'files/blob/' + 'x' * 490,
])
def test_malformed_reference_rejected_before_network(key):
    http = DriveHTTP()
    with pytest.raises(SourceFileError, match='INVALID_DRIVE_REFERENCE'):
        reader(http).open_stream('DRIVE', key)
    assert not http.calls


@pytest.mark.parametrize('changes,code', [
    ({'trashed': True}, 'INVALID_DRIVE_METADATA'),
    ({'mimeType': 'application/vnd.google-apps.shortcut'}, 'DRIVE_SHORTCUT_FORBIDDEN'),
    ({'mimeType': 'application/vnd.google-apps.document'}, 'UNSUPPORTED_DRIVE_NATIVE'),
    ({'mimeType': 'application/vnd.google-apps.spreadsheet'}, 'UNSUPPORTED_DRIVE_NATIVE'),
    ({'mimeType': 'application/vnd.google-apps.presentation'}, 'UNSUPPORTED_DRIVE_NATIVE'),
    ({'name': 'other.txt'}, 'DRIVE_FILENAME_MISMATCH'),
    ({'mimeType': 'application/pdf'}, 'DRIVE_MIME_MISMATCH'),
    ({'size': '999999999'}, 'FILE_TOO_LARGE'),
    ({'size': '-1'}, 'INVALID_DRIVE_METADATA'),
    ({'size': '0'}, 'INVALID_SOURCE_FILE'),
    ({'capabilities': {'canDownload': False}}, 'DRIVE_PERMISSION_DENIED'),
    ({'version': None}, 'INVALID_DRIVE_METADATA'),
    ({'parents': []}, 'DRIVE_OUTSIDE_ROOT'),
    ({'parents': ['other']}, 'DRIVE_OUTSIDE_ROOT'),
])
def test_metadata_security_policy_before_download(changes, code):
    http = DriveHTTP()
    http.metadata['blob'].update(changes)
    http.metadata['other'] = dict(id='other', mimeType=FOLDER, parents=[], trashed=False)
    with pytest.raises(SourceFileError, match=code):
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt')
    assert not any(params.get('alt') == 'media' for _, params in http.calls)
    assert all(response.closed for response in http.responses)


def test_nested_folder_and_parent_shortcut_rejection():
    http = DriveHTTP()
    http.metadata['blob']['parents'] = ['child']
    http.metadata['child'] = dict(id='child', mimeType=FOLDER, parents=['root'], trashed=False)
    with reader(http).open_stream('DRIVE', 'files/blob/sample.txt') as stream:
        assert stream.read() == http.payload
    http.metadata['child']['mimeType'] = 'application/vnd.google-apps.shortcut'
    with pytest.raises(SourceFileError, match='DRIVE_OUTSIDE_ROOT'):
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt')


@pytest.mark.parametrize('status,code,transient', [
    (401, 'DRIVE_CREDENTIALS_EXPIRED', False), (403, 'DRIVE_PERMISSION_DENIED', False),
    (404, 'DRIVE_FILE_NOT_FOUND', False), (302, 'DRIVE_REQUEST_REJECTED', False),
    (429, 'DRIVE_RATE_LIMITED', True), (500, 'DRIVE_UNAVAILABLE', True),
])
def test_http_failures_bounded_and_sanitized(status, code, transient):
    http = DriveHTTP()
    http.statuses = [status] * 3
    with pytest.raises(SourceFileError) as error:
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt')
    assert error.value.code == code and error.value.transient is transient
    assert 'PRIVATE' not in str(error.value)
    assert len(http.calls) == (3 if transient else 1)
    assert all(response.closed for response in http.responses)


def test_transient_recovery_timeout_and_download_cancellation_cleanup(monkeypatch):
    http = DriveHTTP()
    http.statuses = [429, 503]
    with reader(http).open_stream('DRIVE', 'files/blob/sample.txt') as stream:
        assert stream.read() == http.payload
    http = DriveHTTP()
    http.statuses = [TimeoutError()] * 3
    with pytest.raises(SourceFileError, match='DRIVE_UNAVAILABLE'):
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt')
    assert len(http.calls) == 3
    import data_sync_etl.adapters.storage.source_google_drive as module
    original = module.tempfile.TemporaryFile
    staged = []
    def temporary(*args, **kwargs):
        result = original(*args, **kwargs)
        staged.append(result)
        return result
    monkeypatch.setattr(module.tempfile, 'TemporaryFile', temporary)
    http = DriveHTTP()
    http.download_fault = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt')
    assert staged[-1].closed and http.closed and all(r.closed for r in http.responses)
    r = reader(DriveHTTP())
    tick = iter([0, 1000])
    r.clock = lambda: next(tick)
    with pytest.raises(SourceFileError, match='DRIVE_TIMEOUT'):
        r.open_stream('DRIVE', 'files/blob/sample.txt')
    assert staged[-1].closed


@pytest.mark.parametrize('after', [{'version': '2'}, {'parents': ['elsewhere']}, {'trashed': True}])
def test_changed_during_download_is_not_published(after):
    http = DriveHTTP()
    http.after = after
    with pytest.raises(SourceFileError):
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt')


def test_interrupted_download_is_transient_and_temp_is_closed(monkeypatch):
    import data_sync_etl.adapters.storage.source_google_drive as module
    original, staged = module.tempfile.TemporaryFile, []
    def temporary(*args, **kwargs):
        result = original(*args, **kwargs)
        staged.append(result)
        return result
    monkeypatch.setattr(module.tempfile, 'TemporaryFile', temporary)
    http = DriveHTTP()
    http.download_fault = ConnectionError('PRIVATE_TOKEN')
    with pytest.raises(SourceFileError, match='DRIVE_UNAVAILABLE') as error:
        reader(http).open_stream('DRIVE', 'files/blob/sample.txt')
    assert error.value.transient and staged[-1].closed
    assert http.closed and all(r.closed for r in http.responses)


def test_drive_failure_does_not_block_local_in_same_batch(importing):
    c, rows, path = importing
    http = DriveHTTP(path.read_bytes(), name='test.pdf', mime='application/pdf')
    http.statuses = [503] * 3
    dispatcher = SourceFileDispatcher(c.source_files, reader(http))
    c.source_file_import.files = dispatcher
    rows.append({**rows[0], 'source_version_id': 'source-version-2'})
    rows[0] = {**rows[0], 'storage_bucket': 'DRIVE', 'storage_object_key': 'files/blob/test.pdf', 'resource_id': 'resource-2'}
    c.settings.sync_batch_size = 100
    c.source_file_import.discover_once()
    for _ in range(2):
        c.source_file_import.execute(c.source_file_import.claim())
    with c.uow() as repo:
        items = repo.find('source_file_imports', order=('source_version_id',))
        assert items[0]['status'] == 'RETRY_WAIT' and items[0]['last_error_code'] == 'DRIVE_UNAVAILABLE'
        assert items[1]['status'] == 'PROCESSING'
        assert repo.count('resource_versions', {}) == 1


def test_dispatcher_never_falls_back_and_local_works_when_drive_disabled(importing):
    c, _, path = importing
    http = DriveHTTP()
    drive = reader(http)
    drive.settings.source_drive_enabled = False
    local = SourceLocalFileReader(path.parent.parent, c.storage.root)
    dispatcher = SourceFileDispatcher(local, drive)
    with dispatcher.open_stream('books', 'test.pdf') as stream:
        assert stream.read() == path.read_bytes()
    with pytest.raises(SourceFileError, match='DRIVE_DISABLED'):
        dispatcher.open_stream('DRIVE', 'files/blob/sample.txt')
    assert not http.calls
    assert dispatcher.readiness()['local']['status'] == 'LOCAL_READY'
    assert dispatcher.readiness()['google_drive']['error_code'] == 'DRIVE_DISABLED'
    dispatcher.check()
    with pytest.raises(SourceFileError, match='AMBIGUOUS_SOURCE_BUCKET'):
        SourceFileDispatcher(local, drive, local_buckets=['DRIVE'])


def test_local_and_drive_same_queue_pipeline_snapshots_and_hash_gate(importing, caplog):
    caplog.set_level('INFO', logger='etl')
    c, rows, path = importing
    http = DriveHTTP(path.read_bytes(), name='test.pdf', mime='application/pdf')
    c.source_files = SourceFileDispatcher(c.source_files, reader(http))
    c.source_file_import.files = c.source_files
    rows.append({**rows[0], 'source_version_id': 'source-version-2', 'resource_id': 'resource-2',
                 'storage_bucket': 'DRIVE', 'storage_object_key': 'files/blob/test.pdf'})
    c.settings.sync_batch_size = 100
    assert c.source_file_import.discover_once() == 2
    for _ in range(2):
        claimed = c.source_file_import.claim()
        c.source_file_import.execute(claimed)
    for _ in range(10):
        if not c.worker.once('two-source'):
            break
    with c.uow() as repo:
        imports = repo.find('source_file_imports', order=('source_version_id',))
        versions = repo.find('resource_versions')
        assert len(imports) == len(versions) == repo.count('ocr_jobs', {}) == 2
        assert all(i['target_version_id'] and i['processing_job_id'] for i in imports)
        assert len({v['storage_object_key'] for v in versions}) == 1
        assert all(c.orchestrator.status(v['id'])['processing_status'] == 'CONTENT_READY' for v in versions)
        texts = [[repo.get('content_unit_texts', unit['id'])['text'] for unit in
                  repo.find('content_units', {'resource_version_id': v['id']}, order=('sequence_no',))]
                 for v in versions]
        assert texts[0] == texts[1]
    assert c.source_file_import.claim() is None
    assert path.read_bytes() == http.payload
    assert any('"provider": "local"' in record.message for record in caplog.records)
    assert any('"provider": "google_drive"' in record.message for record in caplog.records)
    assert all('blob/test.pdf' not in record.message and 'PRIVATE' not in record.message for record in caplog.records)
    # Same Drive file, new version claims a different SHA: no target/job is created.
    rows.append({**rows[1], 'source_version_id': 'source-version-3', 'source_version_number': 2,
                 'source_hash': '0' * 64})
    c.sync.run()
    c.source_file_import.discover_once()
    claimed = c.source_file_import.claim()
    c.source_file_import.execute(claimed)
    with c.uow() as repo:
        failure = repo.get('source_file_imports', claimed['id'])
        assert failure['last_error_code'] == 'SOURCE_HASH_MISMATCH' and failure['status'] == 'FAILED'
        assert not failure['target_version_id'] and repo.count('resource_versions', {}) == 2


def test_settings_hide_private_config_and_defaults_local_only(container):
    assert isinstance(container.source_files, SourceLocalFileReader)
    settings = Settings(_env_file=None, source_drive_credentials_file='PRIVATE_PATH',
                        source_drive_buckets={'PRIVATE_BUCKET': 'PRIVATE_ROOT'})
    assert 'PRIVATE' not in repr(settings)
    with pytest.raises(ValueError) as error:
        Settings(_env_file=None, source_drive_max_attempts='PRIVATE_TOKEN')
    assert 'PRIVATE_TOKEN' not in str(error.value)
    with pytest.raises(SourceFileError, match='INVALID_DRIVE_BUCKET_CONFIG'):
        SourceGoogleDriveFileReader(settings.model_copy(update={'source_drive_buckets': {'bad/name': 'root'}}))


def test_composition_local_available_without_optional_auth_runtime(importing, monkeypatch):
    import builtins
    c, _, path = importing
    original = builtins.__import__
    def no_google(name, *args, **kwargs):
        if name.startswith('google'):
            raise AssertionError('Local startup must not import Google auth')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', no_google)
    settings = c.settings.model_copy(update={'source_drive_enabled': True, 'source_drive_buckets': {'DRIVE': 'root'}})
    runtime = Container(settings, c.engine, c.documents)
    try:
        assert isinstance(runtime.source_files, SourceFileDispatcher)
        with runtime.source_files.open_stream('books', 'test.pdf') as stream:
            assert stream.read() == path.read_bytes()
        state = runtime.source_file_import.availability()['file_backends']
        assert state['local']['status'] == 'LOCAL_READY'
        assert state['google_drive']['error_code'] == 'DRIVE_CREDENTIALS_UNAVAILABLE'
    finally:
        runtime.close()


@pytest.mark.parametrize('extension', FORMATS)
@pytest.mark.parametrize('provider', ['local', 'google_drive'])
def test_all_supported_formats_share_source_import_pipeline(importing, extension, provider):
    assert_source_format_pipeline(importing, extension, provider, sample(extension))


@pytest.mark.parametrize('provider', ['local', 'google_drive'])
def test_xlsx_default_content_type_import_reaches_content_ready(importing, provider):
    assert_source_format_pipeline(importing, '.xlsx', provider, xlsx_with_default_content_type())


def assert_source_format_pipeline(importing, extension, provider, payload):
    c, rows, original = importing
    from tests.indexing_support import setup_index
    index = setup_index(c)
    path = original.with_name('sample' + extension)
    path.write_bytes(payload)
    rows[0].update(storage_object_key=path.name, source_hash=sha256(payload).hexdigest())
    if extension in ('.png', '.jpg', '.jpeg'):
        c.ocr.engine = ImageEngineDouble()
    if provider == 'google_drive':
        mime = sorted(FormatValidatorRegistry().find_validator(path.name).mime_types)[0]
        http = DriveHTTP(payload, name=path.name, mime=mime)
        c.source_file_import.files = SourceFileDispatcher(c.source_files, reader(http))
        rows[0].update(storage_bucket='DRIVE', storage_object_key=f'files/blob/{path.name}')
    assert c.source_file_worker.once('formats-both-sources')
    for _ in range(20):
        if not c.worker.once('source-format-ocr'):
            break
    with c.uow() as repo:
        item = repo.find('source_file_imports')[0]
        assert item['target_version_id'] and not item['last_error_code']
        version = repo.get('resource_versions', item['target_version_id'])
        assert version['source_hash'] == rows[0]['source_hash']
        assert c.orchestrator.status(version['id'])['processing_status'] == 'CONTENT_READY'
    assert c.storage.get_local_path(version['storage_bucket'], version['storage_object_key']).read_bytes() == payload
    assert path.read_bytes() == payload
    assert index.chunks(version['id'])['total'] > 0
    index.enqueue(version['id'])
    assert index.once()
    assert index.status(version['id'])['index_status'] == 'READY'
    assert index.bags.rows
