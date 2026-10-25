from hashlib import sha256

import pytest

from data_sync_etl.application.metadata_search import MetadataSearchService
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit import test_source_file_import
from tests.unit.test_metadata_search import Embedding, Store

importing = test_source_file_import.importing


def media_payload(extension):
    if extension == '.mp3':
        return (b'\xff\xfb\x90\x00' + bytes(413)) * 2
    def box(kind, payload):
        return (8 + len(payload)).to_bytes(4, 'big') + kind + payload
    return box(b'ftyp', b'isom' + bytes(4) + b'isom') + box(b'moov', b'0') + box(b'mdat', bytes(16))


@pytest.mark.parametrize('extension', ['.mp3', '.mp4'])
@pytest.mark.parametrize('invalid', [False, True])
def test_source_media_registration_keeps_validation_no_ocr(importing, extension, invalid, monkeypatch):
    c, rows, path = importing
    c.settings.source_metadata_media_enabled = True
    formats = FormatValidatorRegistry(media_registration=True)
    c.source_file_import.formats = c.source_resolver.formats = formats
    media = path.with_suffix(extension)
    payload = b'not media' if invalid else media_payload(extension)
    media.write_bytes(payload)
    rows[0].update(storage_object_key=media.name, source_hash=sha256(payload).hexdigest())
    def forbidden(*args, **kwargs):
        pytest.fail('Media registration queued content processing')
    monkeypatch.setattr(c.process_version, 'run', forbidden)
    c.source_file_import.discover_once()
    claimed = c.source_file_import.claim()
    assert claimed is not None
    c.source_file_import.execute(claimed)
    with c.uow() as repo:
        row = repo.get('source_file_imports', claimed['id'])
        assert row['processing_job_id'] is None
        assert not repo.find('ocr_jobs', {})
        if invalid:
            assert row['status'] == 'FAILED' and row['target_version_id'] is None
            return
        assert row['status'] == 'METADATA_ONLY' and row['last_error_code'] == 'UNSUPPORTED_SOURCE_FORMAT'
        version = repo.get('resource_versions', row['target_version_id'])
        assert version['lifecycle_status'] == 'INGESTED'
        assert version['source_hash'] == sha256(payload).hexdigest()
        assert not repo.find('content_units', {'resource_version_id': version['id']})
    assert c.source_file_import.claim() is None
    assert media.read_bytes() == payload
    # The validation step above may read bytes under the existing source policy.
    # Metadata indexing/search must NEVER call the resolver or source reader.
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    monkeypatch.setattr(c.source_resolver, 'temporary_path', forbidden)
    service = MetadataSearchService(c.uow, None, Store(), Embedding(), c.settings)
    assert service.reconcile(apply=True)['ready'] == 1
    from data_sync_etl.domain.concepts import default_dictionary
    items, _, _, _ = service.retrieve('Demo Learning Resource', 'bow', {}, SearchAccess('local_demo'),
        dictionary=default_dictionary(), policy=c.settings.search_expansion)
    assert len(items) == 1 and items[0]['context_id'] is None


@pytest.mark.parametrize('extension', ['.mp3', '.mp4'])
def test_media_hash_failure_never_creates_version(importing, extension):
    c, rows, path = importing
    c.settings.source_metadata_media_enabled = True
    formats = FormatValidatorRegistry(media_registration=True)
    c.source_file_import.formats = c.source_resolver.formats = formats
    media = path.with_suffix(extension)
    media.write_bytes(media_payload(extension))
    rows[0].update(storage_object_key=media.name, source_hash='a' * 64)
    c.source_file_import.discover_once()
    claim = c.source_file_import.claim()
    c.source_file_import.execute(claim)
    with c.uow() as repo:
        row = repo.get('source_file_imports', claim['id'])
        assert row['last_error_code'] == 'SOURCE_HASH_MISMATCH' and row['target_version_id'] is None
