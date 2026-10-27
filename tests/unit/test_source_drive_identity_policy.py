"""Renames are not identities; permissions, type, revision and hash remain gates."""
import pytest

from data_sync_etl.domain.core import SourceFileError
from tests.unit.test_source_google_drive import DriveHTTP, reader


def test_canonical_id_allows_same_type_rename_without_alias():
    http = DriveHTTP(name='RenamedDocument.txt')
    client = reader(http, source_drive_filename_policy='canonical_id')
    with client.open_stream('DRIVE', 'files/blob/sample.txt') as stream:
        assert stream.read() == http.payload
    assert client.filename_aliases == {}


@pytest.mark.parametrize('changes,code', [
    ({'name': 'renamed.pdf'}, 'DRIVE_FILENAME_MISMATCH'),
    ({'mimeType': 'application/pdf'}, 'DRIVE_MIME_MISMATCH'),
    ({'parents': []}, 'DRIVE_OUTSIDE_ROOT'),
    ({'capabilities': {'canDownload': False}}, 'DRIVE_PERMISSION_DENIED'),
    ({'name': '../renamed.txt'}, 'INVALID_DRIVE_REFERENCE'),
])
def test_canonical_id_keeps_all_other_gates(changes, code):
    http = DriveHTTP(name='RenamedDocument.txt')
    http.metadata['blob'].update(changes)
    with pytest.raises(SourceFileError, match=code):
        reader(http, source_drive_filename_policy='canonical_id').open_stream('DRIVE', 'files/blob/sample.txt')


def test_rename_during_download_is_still_a_revision_conflict():
    http = DriveHTTP(name='RenamedDocument.txt')
    http.after = {'name': 'ChangedAgain.txt'}
    with pytest.raises(SourceFileError, match='DRIVE_FILE_CHANGED'):
        reader(http, source_drive_filename_policy='canonical_id').open_stream('DRIVE', 'files/blob/sample.txt')
