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
    assert row['status'] == 'PROCESSING', row
    assert not list((c.storage.root / 'resources').rglob('*'))
    with c.uow() as repo:
        version = repo.get('resource_versions', row['target_version_id'])
    assert version['storage_bucket'] == 'books'
    assert version['storage_object_key'] == 'test.pdf'
    assert version['source_hash'] == sha256(before).hexdigest()
    while c.worker.once('reference-test'):
        pass
    assert c.orchestrator.status(version['id'])['processing_status'] == 'CONTENT_READY'
    assert path.read_bytes() == before
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_flags_fail_closed_never_copy(importing):
    c, _, _ = importing
    c.settings.source_reference_enabled = False
    row = discover_and_import(c)
    assert row['last_error_code'] == 'SOURCE_REFERENCE_DISABLED'
    assert not row['target_version_id']
    c.settings.source_reference_enabled = True
    c.settings.source_temporary_files_enabled = False
    with pytest.raises(SourceFileError, match='SOURCE_TEMP_POLICY_REQUIRED'):
        ref = c.source_resolver.import_reference(row)
        with c.source_resolver.temporary_path(ref):
            pass
    assert not list((c.storage.root / 'resources').rglob('*'))


def test_cleanup_failure_and_no_legacy_fallback(importing):
    c, _, path = importing
    row = discover_and_import(c)
    ref = c.source_resolver.resolve(row['target_version_id'])
    with pytest.raises(RuntimeError):
        with c.source_resolver.temporary_path(ref) as temp:
            assert temp.read_bytes() == path.read_bytes()
            raise RuntimeError('cancel')
    assert not temp.exists()
    # A historical managed copy must never mask deletion of SOURCE.
    with path.open('rb') as stream:
        c.storage.save_stream(stream, suffix='.pdf')
    path.unlink()
    with pytest.raises(SourceFileError, match='FILE_NOT_FOUND'):
        with c.source_resolver.temporary_path(ref):
            pass
    assert list((c.storage.root / 'resources').rglob('*.pdf'))


def test_source_mutates_during_processing_blocks_results(importing):
    c, rows, _ = importing
    discover_and_import(c)
    task = c.ocr.claim('mutation')
    original = c.ocr.pdf.extract_native_text
    def changed(*args):
        result = original(*args)
        rows[0]['source_hash'] = '0' * 64
        return result
    c.ocr.pdf.extract_native_text = changed
    with pytest.raises(SourceFileError, match='SOURCE_VERSION_MUTATED'):
        c.ocr.recognize(task)
    with c.uow() as repo:
        assert repo.count('ocr_page_results', {}) == 0
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


@pytest.mark.parametrize('value', ['https://127.0.0.1/a.pdf', 'http://169.254.169.254/a', 'https://user:secret@host/a.pdf'])
def test_https_contract_disabled_without_network(importing, value):
    c, rows, _ = importing
    rows[0]['storage_object_key'] = value
    row = discover_and_import(c)
    assert row['last_error_code'] == 'INVALID_SOURCE_LINK'
    assert not row['target_version_id']


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
        response = api.get(url)
        assert response.status_code == 200, response.text
        assert response.content == path.read_bytes()
        assert response.headers['cache-control'] == 'private, no-store'
        assert api.get(url, headers={'Range': 'bytes=0-3'}).content == b'%PDF'
        assert api.get(url + '?download=true').headers['content-disposition'].startswith('attachment')
        assert api.get(url + '?view=text').status_code == 200
        info = api.get(url.replace('/file', '/source-info')).json()
        assert not info['can_open_original'] and info['original_url'] is None
        assert 'books' not in str(info) and 'test.pdf' not in str(info)
        source['source_hash'] = '0' * 64
        assert api.get(url).status_code == 409
        source['source_hash'] = version['source_hash']
        path.unlink()
        assert api.get(url).status_code == 404
        assert api.get(url + '?view=text').status_code == 404
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
    assert row['status'] == 'PROCESSING'
    ref = c.source_resolver.resolve(row['target_version_id'])
    info = c.source_resolver.display_reference(ref)
    assert info['can_open_original'] is True
    assert info['original_url'] == 'https://drive.google.com/file/d/blob/view'
    http.metadata['blob']['capabilities']['canDownload'] = False
    with pytest.raises(SourceFileError, match='DRIVE_PERMISSION_DENIED'):
        c.source_resolver.display_reference(ref)
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
        assert response.json()['can_open_original'] is True
        assert response.json()['original_url'] == f'https://drive.google.com/file/d/{file_id}/view'
        assert response.headers.get('cache-control') == 'private, no-store'
        calls = len(http.calls)
        c.settings.search_internal_dev_enabled = False
        denied = api.get(url)
        assert denied.status_code == 404 and 'original_url' not in denied.json()
        assert len(http.calls) == calls
        c.settings.search_internal_dev_enabled = True
        http.metadata[file_id]['capabilities']['canDownload'] = False
        denied = api.get(url)
        assert denied.status_code == 403 and 'original_url' not in denied.json()


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
    """Synthetic fixture measurements; not a real Drive or production benchmark."""
    import json
    import math
    import time
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock

    from test_source_google_drive import DriveHTTP, reader

    from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
    c, rows, path = importing
    row = discover_and_import(c)
    c.settings.source_temp_max_concurrency = 32
    ref = c.source_resolver.resolve(row['target_version_id'])
    measurements = []
    for provider in ('local', 'fake_drive'):
        if provider == 'fake_drive':
            payload = path.read_bytes()
            # Independent fake transport for every session, safe under threads.
            drive = reader(DriveHTTP(payload, name='test.pdf', mime='application/pdf'))
            drive.session_factory = lambda payload=payload: DriveHTTP(payload, name='test.pdf', mime='application/pdf')
            c.source_resolver.files = SourceFileDispatcher(c.source_files, drive)
            rows[0].update(storage_bucket='DRIVE', storage_object_key='files/blob/test.pdf')
            with c.uow() as repo:
                repo.update('source_file_imports', row['id'], {'storage_bucket': 'DRIVE', 'storage_object_key': 'files/blob/test.pdf'})
            ref = c.source_resolver.resolve(row['target_version_id'])
        guard = Lock()
        for clients in (1, 5, 10, 25):
            durations, total_bytes = [], 0
            def read_once(_, reference=ref, guard=guard):
                nonlocal total_bytes
                start = time.perf_counter()
                with c.source_resolver.open_verified_stream(reference) as stream:
                    data = stream.read()
                    assert sha256(data).hexdigest() == reference.expected_sha256
                with guard:
                    total_bytes += len(data)
                return (time.perf_counter() - start) * 1000
            cpu_start = time.process_time()
            with ThreadPoolExecutor(max_workers=clients) as pool:
                durations = list(pool.map(read_once, range(clients * 3)))
            ordered = sorted(durations)
            def percentile(q, ordered=ordered):
                return ordered[max(0, math.ceil(q * len(ordered)) - 1)]
            measurements.append({'provider': provider, 'clients': clients, 'samples': len(ordered),
                                 'p50_ms': percentile(.5), 'p95_ms': percentile(.95), 'p99_ms': percentile(.99),
                                 'read_bytes': total_bytes, 'cpu_seconds': time.process_time() - cpu_start, 'errors': 0})
    (tmp_path / 'source-reference-metrics.json').write_text(json.dumps({'kind': 'synthetic_new_only',
        'baseline': 'NOT_RUN', 'real_drive': 'NOT_RUN', 'rss': 'NOT_MEASURED', 'measurements': measurements}, indent=2))
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


def test_source_and_local_search_scores_content_bags_and_context_parity(importing, tmp_path):
    """Same real PDF fixture, separate SQLite stores and deterministic model double."""
    from conftest import MemoryDocumentStore, migrate

    from data_sync_etl.composition import Container
    from data_sync_etl.db.session import make_engine
    from data_sync_etl.domain.search_access import SearchAccess
    from tests.indexing_support import setup_index
    source, _, path = importing
    engine = make_engine('sqlite:///' + (tmp_path / 'local-parity.db').as_posix())
    migrate(engine)
    local = Container(source.settings.model_copy(update={'storage_root': tmp_path / 'local-managed'}), engine, MemoryDocumentStore())
    try:
        local.sync.run(full=True)
        with local.uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'source_origin': 'LOCAL'})
        local_path = local.storage.get_local_path('input', 'sample.pdf')
        local_path.write_bytes(path.read_bytes())
        indexes = [setup_index(c) for c in (source, local)]
        row = discover_and_import(source)
        local_version = local.ingestion.run('resource-1', 'input', 'sample.pdf')['version']
        local.ocr.create_job(local_version['id'])
        for c, index, version_id in zip((source, local), indexes, (row['target_version_id'], local_version['id']), strict=True):
            while c.worker.once('parity'):
                pass
            index.enqueue(version_id)
            assert index.once()
            index.bags.search_candidates = lambda ids, model, limit, bags=index.bags: [
                doc for doc in bags.rows.values() if doc.resource_version_id in ids and doc.embedding_model == model and doc.is_current][:limit]
        grant = SearchAccess('local_demo')
        fields = ('resource_id', 'score', 'score_components', 'matched_bag_count', 'representation_mode', 'snippet', 'matched_keywords')
        for method in ('bow', 'vector', 'hybrid'):
            responses = [c.search.search('native source import', method, access=grant) for c in (source, local)]
            assert responses[0]['total'] == responses[1]['total'] == 1
            compared = [[tuple(item[field] for field in fields) for item in r['items']] for r in responses]
            assert compared[0] == compared[1]
            contexts = [c.search.context(r['items'][0]['context_id'], access=grant) for c, r in zip((source, local), responses, strict=True)]
            assert [chunk['text'] for chunk in contexts[0]['chunks']] == [chunk['text'] for chunk in contexts[1]['chunks']]
        assert not list((source.storage.root / 'resources').rglob('*'))
        assert list((local.storage.root / 'resources').rglob('*.pdf'))
    finally:
        local.close()


def test_temp_permission_failure_is_redacted_and_retryable(importing, monkeypatch):
    c, _, _ = importing
    row = discover_and_import(c)
    ref = c.source_resolver.resolve(row['target_version_id'])
    def denied():
        raise PermissionError('PRIVATE_TEMP_PATH')
    monkeypatch.setattr(c.source_resolver.temporary, 'root', denied)
    with pytest.raises(SourceFileError, match='SOURCE_TEMP_UNAVAILABLE') as failure:
        with c.source_resolver.temporary_path(ref):
            pass
    assert failure.value.transient
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
async def test_disconnect_while_verifying_source_closes_stream(source_viewer, monkeypatch):
    import io
    import time
    from threading import Event

    import anyio

    from data_sync_etl.api.source_file_response import SourceFileResponse
    c, version, _, path = source_viewer
    started = Event()
    streams = []
    class SlowStream(io.BytesIO):
        def __init__(self, payload, cancelled):
            super().__init__(payload)
            self.cancelled = cancelled
        def read(self, size=-1):
            started.set()
            for _ in range(2000):
                if self.cancelled.is_set():
                    break
                time.sleep(.001)
            return super().read(size)
    def open_stream(bucket, key, *, cancelled=None):
        stream = SlowStream(path.read_bytes(), cancelled)
        streams.append(stream)
        return stream
    monkeypatch.setattr(c.source_files, 'open_stream', open_stream)
    monkeypatch.setattr(c.source_files, 'revision', lambda *args, **kwargs: ('fixture-revision',))
    response = SourceFileResponse(c.source_resolver, c.source_resolver.resolve(version['id']), lambda: None, download=False)
    sent = []
    async def receive():
        assert await anyio.to_thread.run_sync(started.wait, 3)
        return {'type': 'http.disconnect'}
    async def send(message):
        sent.append(message)
    await response({'type': 'http', 'method': 'GET', 'headers': [], 'extensions': {}}, receive, send)
    assert streams and all(stream.closed for stream in streams)
    assert sent == []
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


def test_text_preview_reauthorizes_after_source_verification(source_viewer, monkeypatch):
    from data_sync_etl.domain.search import SearchError
    from tests.unit.test_resource_file import client
    c, version, _, _ = source_viewer
    original = c.search.file_version
    calls = 0
    def revoked(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise SearchError('FILE_NOT_FOUND', 404)
        return original(*args, **kwargs)
    monkeypatch.setattr(c.search, 'file_version', revoked)
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/file?view=text")
        assert response.status_code == 404
        assert response.json()['detail']['code'] == 'FILE_NOT_FOUND'
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
    import subprocess
    from pathlib import Path

    from test_multiformat import sample

    from data_sync_etl.adapters.ocr.engines import TesseractOCREngine
    c, rows, original = importing
    path = original.with_name('sample.png')
    path.write_bytes(sample('.png'))
    rows[0].update(storage_object_key=path.name, source_hash=sha256(path.read_bytes()).hexdigest())
    engine = TesseractOCREngine()
    engine._version = 'fixture-runtime'
    c.ocr.engine = engine
    seen = []
    def recognize(args, **kwargs):
        cwd = Path(kwargs['cwd'])
        assert cwd.parent.parent == c.settings.source_temp_root
        assert (cwd.parent / 'lease').is_file()
        assert (cwd / 'input.png').is_file()
        seen.append(cwd)
        return subprocess.CompletedProcess(args, 0, 'Fixture OCR extraction', '')
    monkeypatch.setattr('data_sync_etl.adapters.ocr.engines.subprocess.run', recognize)
    row = discover_and_import(c)
    assert row['status'] == 'PROCESSING'
    assert c.worker.once('scoped-tesseract')
    assert seen and not seen[0].exists()
    assert not list(c.settings.source_temp_root.glob('attempt-*'))
    assert not list((c.storage.root / 'resources').rglob('*'))
