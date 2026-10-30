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
def test_source_media_registration_is_metadata_only(importing, extension, invalid, monkeypatch):
    c, rows, path = importing
    c.settings.source_metadata_media_enabled = True
    formats = FormatValidatorRegistry(media_registration=True)
    c.source_file_import.formats = c.source_resolver.formats = formats
    media = path.with_suffix(extension)
    payload = b'not media' if invalid else media_payload(extension)
    media.write_bytes(payload)
    rows[0].update(storage_object_key=media.name, source_hash=sha256(payload).hexdigest())
    def forbidden(*args, **kwargs):
        pytest.fail('SOURCE media bytes must not be read or processed')
    monkeypatch.setattr(c.process_version, 'run', forbidden)
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    c.source_file_import.discover_once()
    claimed = c.source_file_import.claim()
    assert claimed is None
    with c.uow() as repo:
        row = repo.find('source_file_imports', {'source_version_id': rows[0]['source_version_id']})[0]
        assert row['processing_job_id'] is None
        assert not repo.find('ocr_jobs', {})
        assert row['status'] == 'METADATA_ONLY'
        version = repo.get('resource_versions', row['target_version_id'])
        assert version['lifecycle_status'] == 'METADATA_ONLY'
        assert version['source_hash'] == sha256(payload).hexdigest()
        assert not repo.find('content_units', {'resource_version_id': version['id']})
    assert media.read_bytes() == payload
    # Metadata indexing/search must never call the resolver or source reader.
    monkeypatch.setattr(c.source_resolver, 'temporary_path', forbidden)
    service = MetadataSearchService(c.uow, None, Store(), Embedding(), c.settings)
    assert service.reconcile(apply=True)['ready'] == 1
    from data_sync_etl.domain.concepts import empty_dictionary
    items, _, _, _ = service.retrieve('Demo Learning Resource', 'bow', {}, SearchAccess('local_demo'),
        dictionary=empty_dictionary(), policy=c.settings.search_expansion)
    assert len(items) == 1 and items[0]['context_id'] is None


@pytest.mark.parametrize('extension', ['.mp3', '.mp4'])
def test_media_hash_is_identity_not_permission_to_read(importing, extension, monkeypatch):
    c, rows, path = importing
    c.settings.source_metadata_media_enabled = True
    formats = FormatValidatorRegistry(media_registration=True)
    c.source_file_import.formats = c.source_resolver.formats = formats
    media = path.with_suffix(extension)
    media.write_bytes(media_payload(extension))
    rows[0].update(storage_object_key=media.name, source_hash='a' * 64)
    monkeypatch.setattr(c.source_files, 'open_stream', lambda *_a, **_k: pytest.fail('No SOURCE media read'))
    c.source_file_import.discover_once()
    claim = c.source_file_import.claim()
    assert claim is None
    with c.uow() as repo:
        row = repo.find('source_file_imports', {'source_version_id': rows[0]['source_version_id']})[0]
        assert row['status'] == 'METADATA_ONLY' and row['source_hash'] == 'a' * 64
        assert row['target_version_id'] is not None
        version = repo.get('resource_versions', row['target_version_id'])
        assert version['lifecycle_status'] == 'METADATA_ONLY'
        assert version['source_hash'] == 'a' * 64
        assert repo.count('ocr_jobs', {}) == 0
