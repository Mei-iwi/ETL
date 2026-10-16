"""Opt-in live Drive READ test. No upload, export, database or schema writes."""

import os

import pytest

from data_sync_etl.adapters.storage.source_google_drive import SourceGoogleDriveFileReader
from data_sync_etl.config import Settings
from data_sync_etl.domain.file_integrity import stream_hash


def test_live_drive_readonly_sha256():
    names = ('ETL_TEST_DRIVE_CREDENTIALS_FILE', 'ETL_TEST_DRIVE_ROOT_ID',
             'ETL_TEST_DRIVE_FILE_ID', 'ETL_TEST_DRIVE_FILE_NAME', 'ETL_TEST_DRIVE_SHA256')
    if not all(os.getenv(name) for name in names):
        pytest.skip('NOT_RUN: explicit Dev Drive test credential/root/blob/SHA-256 required')
    if os.getenv('ETL_TEST_DRIVE_ENVIRONMENT') != 'development':
        pytest.fail('Live Drive test requires explicit Development approval', pytrace=False)
    credentials, root, file_id, filename, expected = [os.environ[name] for name in names]
    settings = Settings(_env_file=None, source_drive_enabled=True,
        source_drive_credentials_file=credentials, source_drive_buckets={'TEST_DRIVE': root},
        source_drive_auth_type=os.getenv('ETL_TEST_DRIVE_AUTH_TYPE', 'service_account'))
    reader = SourceGoogleDriveFileReader(settings)
    reader.check()
    with reader.open_stream('TEST_DRIVE', f'files/{file_id}/{filename}') as stream:
        actual = stream_hash(stream)
    assert actual == expected.lower(), 'Dev Drive bytes do not match approved SHA-256'
