"""Real isolated SOURCE/Master PG and existing worker; Drive HTTP is an explicit double."""

import json
from hashlib import sha256

import pytest
from sqlalchemy import text

from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
from data_sync_etl.adapters.storage.source_google_drive import (
    API,
    FOLDER,
    SourceGoogleDriveFileReader,
)

from .test_file_import_flow import setup_import

pytestmark = pytest.mark.postgres


def test_postgres_local_and_drive_same_batch_same_snapshot(source, pg_container, tmp_path):
    c = pg_container
    path = setup_import(source, c, tmp_path)
    payload = path.read_bytes()

    class Response:
        status_code = 200
        def __init__(self, body):
            self.body = body
        def iter_content(self, size):
            for i in range(0, len(self.body), size):
                yield self.body[i:i + size]
        def close(self):
            pass

    class HTTP:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def get(self, url, **kwargs):
            if kwargs['params'].get('alt') == 'media':
                return Response(payload)
            data = (dict(id='root', mimeType=FOLDER, trashed=False) if url == API + 'root' else
                    dict(id='blob', name='native.pdf', mimeType='application/pdf', size=str(len(payload)),
                         version='1', parents=['root'], trashed=False, capabilities={'canDownload': True}))
            return Response(json.dumps(data).encode())

    settings = c.settings.model_copy(update={'source_drive_enabled': True, 'source_drive_buckets': {'DRIVE': 'root'}})
    drive = SourceGoogleDriveFileReader(settings, session_factory=HTTP)
    c.source_file_import.files = SourceFileDispatcher(c.source_files, drive)
    with source.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO resource_versions (id,resource_id,version_number,lifecycle_status,storage_bucket,"
            "storage_object_key,source_hash,uploaded_by,created_at,updated_at) "
            "SELECT 'source-drive-2','resource-2',1,'APPROVED','DRIVE','files/blob/native.pdf',:hash,"
            "uploaded_by,now(),now() FROM resource_versions WHERE id='source-local-1'"
        ), {'hash': sha256(payload).hexdigest()})
    c.sync.run(full=True)
    for _ in range(4):
        c.source_file_worker.once('dual-source-test')
    for _ in range(10):
        if not c.worker.once('dual-source-ocr'):
            break
    with c.uow() as repo:
        versions = repo.find('resource_versions')
        assert len(versions) == repo.count('ocr_jobs', {}) == repo.count('source_file_imports', {}) == 2
        assert len({v['storage_object_key'] for v in versions}) == 1
        assert all(c.orchestrator.status(v['id'])['processing_status'] == 'CONTENT_READY' for v in versions)
    assert not c.source_file_worker.once('idempotent-dual-source')
    assert path.read_bytes() == payload
