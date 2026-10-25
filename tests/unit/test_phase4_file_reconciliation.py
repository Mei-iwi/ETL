from data_sync_etl.application.reconciliation_engine import (
    FLAG_DESYNC_CHECKSUM,
    FLAG_NOT_VERIFIED,
    FLAG_SYNCED,
)
from data_sync_etl.main import create_app
from time import monotonic, sleep
from tests.local_admin_client import TestClient
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_reference import source_viewer as source_viewer


def test_source_reconciliation_reads_actual_source_and_retains_no_copy(source_viewer):
    c, version, source, path = source_viewer
    content = path.read_bytes()
    with c.uow() as repo:
        before = repo.get('resource_versions', version['id'])
        result = c.reconciliation_engine.verify_file_blobs(repo.session)
    selected = next(row for row in result if row['record_id'] == version['id'])
    assert selected['discrepancy_flag'] == FLAG_SYNCED
    assert selected['target_value']['verification'] == 'SOURCE_STREAM_SHA256'
    assert selected['target_value']['verified_size'] == len(content)
    assert path.read_bytes() == content
    assert not list(c.settings.source_temp_root.glob('attempt-*'))
    with c.uow() as repo:
        assert repo.get('resource_versions', version['id']) == before


def test_unverified_source_rows_are_visible_but_not_counted_as_corrupt(source_viewer):
    c, version, _, _ = source_viewer
    c.settings.source_reference_enabled = False
    with TestClient(create_app(c)) as client:
        response = client.post('/api/v1/reconciliation/runs', json={'stream': 'files'})
        assert response.status_code == 202
        end = monotonic() + 10
        while True:
            report = client.get('/api/v1/reconciliation/runs/latest?stream=files').json()['stream_reports'][0]
            if report['status'] != 'RUNNING' or monotonic() > end:
                break
            sleep(.02)
        assert report['status'] == 'PARTIAL' and report['mismatched_count'] == 0
        assert report['summary_meta']['unverified_source_files'] == 1
        rows = client.get('/api/v1/reconciliation/discrepancies?latest_only=true&stream=files&discrepancy_flag=NOT_VERIFIED').json()
        assert rows['total'] == 1 and rows['items'][0]['record_id'] == version['id']
        assert rows['items'][0]['target_value']['reason_code'] == 'SOURCE_REFERENCE_DISABLED'


def test_corrupt_source_cannot_be_masked_by_a_managed_copy(source_viewer):
    c, version, _, path = source_viewer
    content = path.read_bytes()
    with path.open('rb') as stream:
        c.storage.save_stream(stream, suffix='.pdf')
    path.write_bytes(content.replace(b'%PDF-', b'%BAD-', 1))
    with c.uow() as repo:
        result = c.reconciliation_engine.verify_file_blobs(repo.session)
    selected = next(row for row in result if row['record_id'] == version['id'])
    assert selected['discrepancy_flag'] == FLAG_DESYNC_CHECKSUM
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


def test_file_budget_is_explicit_and_does_not_claim_integrity(source_viewer, monkeypatch):
    c, version, _, _ = source_viewer
    c.reconciliation_engine.file_read_budget_seconds = 0
    monkeypatch.setattr(c.source_resolver, 'resolve', lambda *_: (_ for _ in ()).throw(AssertionError('No read after budget')))
    with c.uow() as repo:
        result = c.reconciliation_engine.verify_file_blobs(repo.session)
    row = next(row for row in result if row['record_id'] == version['id'])
    assert row['discrepancy_flag'] == FLAG_NOT_VERIFIED
    assert row['target_value']['reason_code'] == 'RECONCILIATION_READ_BUDGET'
