import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

import pymupdf
import pytest
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from test_multiformat import sample

from data_sync_etl.application.file_preview import LibreOfficePdfConverter, spreadsheet_preview
from data_sync_etl.domain.core import SourceFileError
from tests.unit.test_resource_file import client
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_reference import source_viewer as source_viewer


def set_format(c, version, path, extension, body, source=None):
    from hashlib import sha256

    from data_sync_etl.domain.search import SearchError
    destination = path.with_suffix(extension)
    destination.write_bytes(body)
    digest = sha256(body).hexdigest()
    with c.uow() as repo:
        repo.update('resource_versions', version['id'], {'storage_object_key': destination.name if source else str(Path(version['storage_object_key']).with_suffix(extension)).replace('\\', '/'), 'source_hash': digest})
        if source:
            source.update(storage_object_key=destination.name, source_hash=digest)
            row = repo.find('source_file_imports', {'target_version_id': version['id']})[0]
            repo.update('source_file_imports', row['id'], {'storage_object_key': destination.name, 'source_hash': digest})
    # Preview fixtures replace the indexed PDF with another format. Keep an
    # explicit grant fixture; unchanged index/hash ACL tests run separately.
    def fixture_grant(version_id, **kwargs):
        if version_id != version['id'] or not c.settings.search_internal_dev_enabled:
            raise SearchError('FILE_NOT_FOUND', 404)
        with c.uow() as repo:
            return repo.get('resource_versions', version_id)
    c.search.file_version = fixture_grant
    return destination


@pytest.mark.parametrize('extension', ['.docx', '.pptx'])
def test_preview_pdf_keeps_original_download_and_cleans_source(source_viewer, monkeypatch, extension):
    c, version, source, path = source_viewer
    body = sample(extension)
    original = set_format(c, version, path, extension, body, source)
    def forbidden(*_args, **_kwargs):
        pytest.fail('SOURCE preview/download is disabled')
    monkeypatch.setattr(c.file_previews.converter, 'convert', forbidden)
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    monkeypatch.setattr(c.storage, 'get_local_path', forbidden)
    url = f"/api/v1/resource-versions/{version['id']}/file"
    with client(c) as api:
        for query in ['?view=preview', '?download=true', '?download=true&view=preview', '?view=text']:
            response = api.get(url + query)
            assert response.status_code == 409
            expected_code = 'FILE_NOT_READY' if query == '?view=text' else 'SOURCE_OPEN_UNSUPPORTED'
            assert response.json()['detail']['code'] == expected_code
        c.settings.search_internal_dev_enabled = False
        assert api.get(url + '?view=preview').status_code == 404
    assert original.read_bytes() == body
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


@pytest.mark.parametrize('extension', ['.xlsx', '.csv'])
def test_source_grid_and_original_download(source_viewer, extension):
    c, version, source, path = source_viewer
    body = sample(extension)
    original = set_format(c, version, path, extension, body, source)
    url = f"/api/v1/resource-versions/{version['id']}/file"
    with client(c) as api:
        response = api.get(url + '?view=preview')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        download = api.get(url + '?download=true&view=preview')
        assert download.status_code == 409
        assert download.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
    assert original.read_bytes() == body
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_preview_failure_has_code_and_no_leaked_path(source_viewer, monkeypatch):
    c, version, source, path = source_viewer
    set_format(c, version, path, '.docx', sample('.docx'), source)
    monkeypatch.setattr(c.file_previews.converter, 'convert', lambda *_a, **_k: pytest.fail('SOURCE preview disabled'))
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/file?view=preview")
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        assert str(path) not in response.text
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_grid_limits_styles_formula_no_execution(tmp_path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = 'First'
    sheet.append(['Heading', '=1+2', '<script>alert(1)</script>'])
    sheet['A1'].font = Font(bold=True, color='FF123456')
    sheet['A1'].fill = PatternFill('solid', fgColor='FFABCDEF')
    sheet['CV502'] = 'beyond limit'
    workbook.create_sheet('Second').append(['Other sheet'])
    path = tmp_path / 'grid.xlsx'
    workbook.save(path)
    value = spreadsheet_preview(path)
    assert value['truncated'] is True
    cell = value['sheets'][0]['rows'][0][0]
    assert cell['bold'] and cell['color'] == '#123456' and cell['background'] == '#ABCDEF'
    assert value['sheets'][0]['rows'][0][1]['text'] == '=1+2'
    assert len(value['sheets'][0]['rows']) == 500
    assert len(value['sheets'][0]['rows'][0]) == 100
    assert value['sheets'][1]['name'] == 'Second'
    assert len(json.dumps(value)) < 30 * 1024 * 1024


def test_csv_quoted_newlines_delimiter_bom_and_cancel(tmp_path):
    path = tmp_path / 'grid.csv'
    path.write_text('Tên;Nội dung\n"Một;hai";"dòng một\ndòng hai"\n', encoding='utf-8-sig')
    result = spreadsheet_preview(path)
    assert result['sheets'][0]['rows'][1][0]['text'] == 'Một;hai'
    assert result['sheets'][0]['rows'][1][1]['text'] == 'dòng một\ndòng hai'
    cancelled = Event()
    cancelled.set()
    with pytest.raises(SourceFileError, match='SOURCE_REQUEST_CANCELLED'):
        spreadsheet_preview(path, cancelled)


@pytest.mark.parametrize('extension', ['.docx', '.pptx'])
def test_real_libreoffice_pdf(tmp_path, extension):
    executable = os.environ.get('ETL_TEST_OFFICE_PDF_EXECUTABLE')
    if not executable:
        pytest.skip('Explicit isolated LibreOffice runtime not configured')
    source = tmp_path / ('fixture' + extension)
    source.write_bytes(sample(extension))
    original = source.read_bytes()
    converter = LibreOfficePdfConverter(SimpleNamespace(office_pdf_executable=Path(executable), office_pdf_timeout_seconds=60))
    output = converter.convert(source, tmp_path)
    with pymupdf.open(output) as pdf:
        assert pdf.page_count >= 1
        assert any(page.get_text().strip() for page in pdf)
    assert source.read_bytes() == original


def test_converter_rejects_external_resource_before_launch(tmp_path):
    source = tmp_path / 'linked.docx'
    with ZipFile(source, 'w') as archive:
        archive.writestr('word/_rels/document.xml.rels', '<Relationships><Relationship TargetMode="External" Type="image" Target="http://127.0.0.1/private"/></Relationships>')
    converter = LibreOfficePdfConverter(SimpleNamespace(office_pdf_executable=Path(__file__), office_pdf_timeout_seconds=5))
    with pytest.raises(SourceFileError, match='PREVIEW_EXTERNAL_CONTENT'):
        converter.convert(source, tmp_path)


@pytest.mark.parametrize('mode, code', [('cancel', 'SOURCE_REQUEST_CANCELLED'), ('timeout', 'PREVIEW_TIMEOUT')])
def test_converter_cancellation_timeout_stop_own_process(tmp_path, monkeypatch, mode, code):
    source = tmp_path / 'fixture.docx'
    source.write_bytes(sample('.docx'))
    cancelled, stopped = Event(), []
    process = SimpleNamespace(poll=lambda: None)
    def launch(*args, **kwargs):
        if mode == 'cancel':
            cancelled.set()
        return process
    monkeypatch.setattr('data_sync_etl.application.file_preview.subprocess.Popen', launch)
    converter = LibreOfficePdfConverter(SimpleNamespace(office_pdf_executable=Path(__file__), office_pdf_timeout_seconds=5))
    monkeypatch.setattr(converter, '_stop', lambda item: stopped.append(item))
    clock = iter([0, 10])
    monkeypatch.setattr('data_sync_etl.application.file_preview.time.monotonic', lambda: next(clock))
    with pytest.raises(SourceFileError, match=code):
        converter.convert(source, tmp_path, cancelled)
    assert stopped == [process]


def test_local_preview_works_with_source_flags_off_and_keeps_original(searchable, monkeypatch):
    c, version = searchable
    path = c.storage.get_local_path(version['storage_bucket'], version['storage_object_key'])
    body = sample('.docx')
    original = set_format(c, version, path, '.docx', body)
    c.settings.source_reference_enabled = c.settings.source_temporary_files_enabled = False
    outputs = []
    with pymupdf.open() as pdf:
        pdf.new_page()
        converted = pdf.tobytes()
    def convert(path, directory, cancelled=None):
        assert path == original
        output = directory / 'preview.pdf'
        output.write_bytes(converted)
        outputs.append(output)
        return output
    monkeypatch.setattr(c.file_previews.converter, 'convert', convert)
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/file?view=preview")
        assert response.status_code == 200 and response.content == converted
        assert api.get(f"/api/v1/resource-versions/{version['id']}/file?download=true").content == body
    assert original.read_bytes() == body
    assert outputs and not outputs[0].exists()


def test_preview_is_blocked_before_source_content_access(source_viewer, monkeypatch):
    c, version, source, path = source_viewer
    set_format(c, version, path, '.docx', sample('.docx'), source)
    monkeypatch.setattr(c.file_previews.converter, 'convert', lambda *_a, **_k: pytest.fail('SOURCE preview disabled'))
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/file?view=preview")
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_orphan_preview_profile_cleanup_stays_inside_attempt(source_viewer):
    import time
    c, _, _, source = source_viewer
    original = source.read_bytes()
    temporary = c.source_resolver.temporary
    orphan = temporary.root() / 'attempt-abandoned-preview'
    profile = orphan / 'etl-preview-fixture/profile/user'
    profile.mkdir(parents=True)
    (orphan / 'lease').write_bytes(b'0')
    (profile / 'registrymodifications.xcu').write_text('fixture')
    os.utime(orphan, (time.time() - 7200,) * 2)
    assert temporary.cleanup_orphans() == 1
    assert not orphan.exists() and source.read_bytes() == original


def test_source_revoked_during_conversion_never_sends_pdf(source_viewer, monkeypatch):
    c, version, source, path = source_viewer
    set_format(c, version, path, '.docx', sample('.docx'), source)
    source['source_lifecycle_status'] = 'REJECTED'
    with c.uow() as repo:
        imported = repo.find('source_file_imports', {'target_version_id': version['id']})[0]
        repo.update('source_file_imports', imported['id'], {'source_lifecycle_status': 'REJECTED'})
    monkeypatch.setattr(c.file_previews.converter, 'convert', lambda *_a, **_k: pytest.fail('SOURCE preview disabled'))
    with client(c) as api:
        response = api.get(f"/api/v1/resource-versions/{version['id']}/file?view=preview")
        assert response.status_code == 403
        assert response.json()['detail']['code'] == 'BLOCKED_SOURCE_POLICY'
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_converter_busy_fails_before_launch(tmp_path):
    converter = LibreOfficePdfConverter(SimpleNamespace(source_temp_max_concurrency=1))
    assert converter.slots.acquire(blocking=False)
    try:
        with pytest.raises(SourceFileError, match='PREVIEW_BUSY'):
            converter.convert(tmp_path / 'missing.docx', tmp_path)
    finally:
        converter.slots.release()
