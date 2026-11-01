import io

import pytest

from data_sync_etl.adapters.storage.object import (
    CloudStorageAdapter,
    InMemoryObjectStorageAdapter,
    S3CompatibleObjectStorageAdapter,
)
from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
from data_sync_etl.adapters.storage.source_object import SourceObjectStorageReader
from data_sync_etl.application.source_open import SourceOpenResolver
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError, SourceFileError


def test_fake_provider_supports_upload_head_stream_and_private_presign():
    cloud = InMemoryObjectStorageAdapter(provider_id='neon-staging', bucket='learning')
    storage = CloudStorageAdapter(cloud, object_prefix='learning-resources/')
    stored = storage.save_stream(io.BytesIO(b'hello'), suffix='.txt')
    assert stored.bucket == 'learning'
    assert storage.exists(stored.bucket, stored.object_key)
    with storage.open_stream(stored.bucket, stored.object_key) as stream:
        assert stream.read() == b'hello'
    with storage.temporary_path(stored.bucket, stored.object_key) as path:
        assert path.read_bytes() == b'hello'
    assert cloud.presign_read(stored.object_key, ttl_seconds=60).startswith('https://')


def test_cloud_storage_rejects_path_traversal_and_no_local_path():
    cloud = InMemoryObjectStorageAdapter(bucket='learning')
    storage = CloudStorageAdapter(cloud)
    with pytest.raises(DomainError, match='CLOUD_STORAGE_INVALID_OBJECT_KEY'):
        cloud.get_stream('../secret')
    with pytest.raises(DomainError, match='CLOUD_STORAGE_NO_LOCAL_PATH'):
        storage.get_local_path('learning', 'a.txt')


def test_object_storage_settings_require_private_endpoint_and_secret():
    with pytest.raises(ValueError, match='bucket'):
        Settings(_env_file=None, etl_file_storage_mode='object')
    settings = Settings(
        _env_file=None,
        etl_file_storage_mode='object',
        etl_file_storage_endpoint_url='https://s3.example.invalid',
        etl_file_storage_bucket='learning',
        etl_file_storage_access_key_id='access',
        etl_file_storage_secret_access_key='secret',
    )
    assert settings.etl_file_storage_provider == 'neon'


def test_production_cannot_use_local_file_storage():
    with pytest.raises(ValueError, match='local'):
        Settings(_env_file=None, app_environment='production')


def test_s3_provider_signing_is_endpoint_driven_and_does_not_leak_secret():
    adapter = S3CompatibleObjectStorageAdapter(
        provider='r2', provider_id='r2-current', endpoint_url='https://account.r2.cloudflarestorage.com',
        region='auto', bucket='private', access_key_id='ACCESS', secret_access_key='SECRET',
    )
    url, headers = adapter._sign('HEAD', 'learning-resources/a.txt')
    assert 'account.r2.cloudflarestorage.com/private/learning-resources/a.txt' in url
    assert 'SECRET' not in url and 'SECRET' not in str(headers)
    assert headers['x-amz-content-sha256'] == 'UNSIGNED-PAYLOAD'
    signed = adapter.presign_read('learning-resources/a.txt', ttl_seconds=60)
    assert SourceOpenResolver._allowed_viewer(signed, {'account.r2.cloudflarestorage.com'})


def test_provider_swap_keeps_the_same_port_and_does_not_cross_read_objects():
    neon = CloudStorageAdapter(InMemoryObjectStorageAdapter(provider='neon', provider_id='neon-v1', bucket='learning'))
    r2 = CloudStorageAdapter(InMemoryObjectStorageAdapter(provider='r2', provider_id='r2-v1', bucket='learning'))
    old = neon.save_stream(io.BytesIO(b'old'), suffix='.txt')
    new = r2.save_stream(io.BytesIO(b'new'), suffix='.txt')
    assert neon.open_stream(old.bucket, old.object_key).read() == b'old'
    assert r2.open_stream(new.bucket, new.object_key).read() == b'new'
    with pytest.raises(DomainError, match='OBJECT_NOT_FOUND'):
        r2.open_stream(old.bucket, old.object_key)


def test_source_object_reader_streams_allowlisted_bucket_without_local_copy():
    cloud = InMemoryObjectStorageAdapter(provider='neon', provider_id='source-neon', bucket='assets')
    cloud.put_object(io.BytesIO(b'source-bytes'), object_key='uploads/source.pdf',
                     content_type='application/pdf')
    source = SourceObjectStorageReader(cloud)
    dispatcher = SourceFileDispatcher(None, type('Drive', (), {'roots': {}})(),
                                      object_reader=source)
    with dispatcher.open_stream('assets', 'uploads/source.pdf') as stream:
        assert stream.read() == b'source-bytes'
    assert dispatcher.revision('assets', 'uploads/source.pdf')[0] == len(b'source-bytes')
    viewer = dispatcher.viewer_reference('assets', 'uploads/source.pdf')
    assert viewer['file_name'] == 'source.pdf'
    assert viewer['source_file_id'] != 'uploads/source.pdf'
    assert viewer['viewer_url'].startswith('https://')
    with pytest.raises(SourceFileError, match='SOURCE_BUCKET_NOT_ALLOWED'):
        dispatcher.open_stream('uploads', 'source.pdf')
