import pytest
from fastapi.testclient import TestClient

from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


def client(c):
    return TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000))


@pytest.mark.parametrize('download', [False, True])
@pytest.mark.parametrize('searchable', [{'unreviewed': True}], indirect=True)
def test_file_view_download_and_range(searchable, download):
    c, version = searchable
    with client(c) as api:
        path = f'/api/v1/resource-versions/{version["id"]}/file?download={str(download).lower()}'
        response = api.get(path)
        assert response.status_code == 200
        assert response.headers['content-type'] == 'application/pdf'
        assert response.content.startswith(b'%PDF')
        assert response.headers['content-disposition'].startswith('attachment' if download else 'inline')
        assert response.headers['cache-control'] == 'private, no-store'
        assert 'storage' not in response.headers['content-disposition']
        preview = api.get(path + '&view=text')
        assert preview.status_code == 200 and preview.headers['content-type'].startswith('text/plain')
        assert 'Page' in preview.text
        partial = api.get(path, headers={'Range': 'bytes=0-3'})
        assert partial.status_code == 206 and partial.content == b'%PDF'
        assert api.get(path, headers={'X-Forwarded-For': '127.0.0.1'}).status_code == 403
        c.settings.search_internal_dev_enabled = False
        assert api.get(path).status_code == 404
        assert api.get(path + '&view=text').status_code == 404


@pytest.mark.parametrize('change', ['inactive', 'draft', 'rejected', 'not_ready', 'rejected_not_ready', 'missing_file', 'missing_version'])
def test_file_never_bypasses_policy(searchable, change):
    c, version = searchable
    version_id = version['id']
    with c.uow() as repo:
        if change in ('inactive', 'draft'):
            repo.update('master_learning_resources', version['resource_id'],
                        {'is_active': False} if change == 'inactive' else {'publication_status': 'DRAFT'})
        if change in ('rejected', 'rejected_not_ready'):
            unit = repo.find('content_units', {'resource_version_id': version_id})[0]
            repo.update('content_units', unit['id'], {'review_status': 'REJECTED'})
        if change in ('not_ready', 'rejected_not_ready'):
            repo.update('etl_stage_runs', c.indexing._runs(repo, version_id)[0]['id'], {'status': 'FAILED'})
    if change == 'missing_file':
        c.storage.exists = lambda *args: False
    if change == 'missing_version':
        version_id = 'not-found'
    with client(c) as api:
        response = api.get(f'/api/v1/resource-versions/{version_id}/file')
        expected = 409 if change == 'not_ready' else 404
        assert response.status_code == expected
        if change == 'not_ready':
            assert response.json()['detail']['code'] == 'FILE_NOT_READY'
        assert not response.content.startswith(b'%PDF')
        if change != 'missing_file':
            assert api.get(f'/api/v1/resource-versions/{version_id}/file?view=text').status_code == expected
        assert api.get(f'/api/v1/resource-versions/{version_id}/file?view=preview').status_code == expected
