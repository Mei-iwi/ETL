"""Media MIME/range/download coverage with explicit isolated file grants."""
import pytest

from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from tests.unit.test_file_preview import set_format
from tests.unit.test_metadata_media_registration import media_payload
from tests.unit.test_resource_file import client
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_reference import source_viewer as source_viewer


@pytest.mark.parametrize('extension,mime', [('.mp3', 'audio/mpeg'), ('.mp4', 'video/mp4')])
@pytest.mark.parametrize('ownership', ['local', 'source'])
def test_media_preview_and_download_preserve_bytes_mime_and_access(request, extension, mime, ownership):
    if ownership == 'source':
        c, version, source, path = request.getfixturevalue('source_viewer')
    else:
        c, version = request.getfixturevalue('searchable')
        path = c.storage.get_local_path(version['storage_bucket'], version['storage_object_key'])
        source = None
    body = media_payload(extension)
    original = set_format(c, version, path, extension, body, source)
    c.source_resolver.formats = FormatValidatorRegistry(media_registration=True)
    url = f"/api/v1/resource-versions/{version['id']}/file"
    with client(c) as api:
        for query in ('?view=preview', '?download=true'):
            response = api.get(url + query)
            assert response.status_code == 200, response.text
            assert response.content == body and response.headers['content-type'] == mime
            assert response.headers['cache-control'] == 'private, no-store'
            assert response.headers['x-content-type-options'] == 'nosniff'
            assert response.headers['content-disposition'].startswith('attachment' if 'download' in query else 'inline')
        response = api.get(url + '?view=preview', headers={'Range': 'bytes=0-3'})
        assert response.status_code == 206 and response.content == body[:4]
        c.settings.search_internal_dev_enabled = False
        assert api.get(url + '?view=preview').status_code == 404
    assert original.read_bytes() == body
