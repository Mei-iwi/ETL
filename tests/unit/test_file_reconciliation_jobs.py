from contextlib import contextmanager
from copy import copy
from threading import Event, Lock
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

from data_sync_etl.application.file_reconciliation import FileReconciliationJobs
from data_sync_etl.db.models import SyncReconciliationRun
from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_reference import source_viewer as source_viewer


def finished(c, timeout=10):
    end = monotonic() + timeout
    while monotonic() < end:
        with c.uow() as repo:
            run = repo.session.query(SyncReconciliationRun).filter_by(stream_name='files').order_by(SyncReconciliationRun.started_at.desc()).first()
            if run and run.status != 'RUNNING':
                return run
        sleep(.02)
    raise AssertionError('Background file audit did not terminate')


def test_accepted_response_and_durable_progress_do_not_wait_for_source(source_viewer, monkeypatch):
    c, _, _, _ = source_viewer
    entered, release = Event(), Event()
    original = c.reconciliation_engine.verify_file_snapshot
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(c.reconciliation_engine, 'verify_file_snapshot', slow)
    try:
        with TestClient(create_app(c)) as client:
            started = client.post('/api/v1/reconciliation/runs', json={'stream': 'files', 'mode': 'FULL'})
            assert started.status_code == 202 and started.json()['status'] == 'RUNNING'
            assert entered.wait(2)
            active = client.get('/api/v1/reconciliation/runs/latest?stream=files').json()['stream_reports'][0]
            assert active['status'] == 'RUNNING'
            assert active['summary_meta']['processed_files'] == 0
            assert client.post('/api/v1/reconciliation/runs', json={'stream': 'files'}).status_code == 409
            release.set()
            run = finished(c)
            assert run.matched_count == 1
            assert run.summary_meta['processed_files'] == run.summary_meta['total_files'] == 1
    finally:
        release.set()
        c.reconciliation_jobs.close()


def test_each_file_has_its_own_deadline(source_viewer, monkeypatch):
    c, version, _, path = source_viewer
    with c.uow() as repo:
        copy = {key: value for key, value in repo.get('resource_versions', version['id']).items()
                if key not in ('id', 'created_at', 'updated_at')}
        copy['version_number'] += 1
        repo.insert('resource_versions', copy)
    clock = [0.]
    monkeypatch.setattr('data_sync_etl.adapters.persistence.reconciliation_engine.monotonic', lambda: clock[0])
    c.reconciliation_engine.file_read_budget_seconds = 5
    monkeypatch.setattr(c.source_resolver, 'resolve', lambda _: SimpleNamespace(
        ownership='SOURCE_BACKED', provider='local'))
    @contextmanager
    def read(ref, *, cancelled):
        clock[0] += 4
        assert not cancelled.is_set(), 'Previous file consumed this file deadline'
        yield path
    monkeypatch.setattr(c.source_resolver, 'temporary_path', read)
    with c.uow() as repo:
        result = c.reconciliation_engine.verify_file_blobs(repo.session)
    assert len(result) == 2 and all(row['discrepancy_flag'] == 'SYNCED' for row in result)


def test_database_fence_prevents_second_api_owner(source_viewer, monkeypatch):
    c, _, _, _ = source_viewer
    entered, release = Event(), Event()
    original = c.reconciliation_engine.verify_file_snapshot
    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(c.reconciliation_engine, 'verify_file_snapshot', slow)
    other_engine = copy(c.reconciliation_engine)
    other_engine._run_lock = Lock()  # Separate API instance; only the DB/UoW fence is shared.
    other = FileReconciliationJobs(other_engine)
    try:
        c.reconciliation_jobs.start(mode='FULL')
        assert entered.wait(2)
        assert not other.can_recover()
        with pytest.raises(DomainError, match='RECONCILIATION_BUSY'):
            other.start()
        release.set()
        assert finished(c).matched_count == 1
    finally:
        release.set()
        c.reconciliation_jobs.close()
        other.close()


def test_interrupted_run_resumes_unprocessed_versions_first(source_viewer, monkeypatch):
    c, version, _, _ = source_viewer
    with c.uow() as repo:
        copy = {key: value for key, value in repo.get('resource_versions', version['id']).items()
                if key not in ('id', 'created_at', 'updated_at')}
        copy['version_number'] += 1
        second = repo.insert('resource_versions', copy)
        ids = sorted([version['id'], second['id']])
        previous = SyncReconciliationRun(run_id=new_id(), stream_name='files', status='RUNNING',
            started_at=now(), target_count=2, summary_meta={'processed_files': 1, 'total_files': 2, 'last_processed_id': ids[0]})
        repo.session.add(previous)
        old_id = previous.run_id
    visited = []
    def verify(versions, origins, *, audit_type='DEEP'):
        visited.extend(version.id for version in versions)
        return [{'discrepancy_flag': 'SYNCED'}]
    monkeypatch.setattr(c.reconciliation_engine, 'verify_file_snapshot', verify)
    with TestClient(create_app(c)) as client:
        prior = client.get('/api/v1/reconciliation/runs/latest?stream=files').json()['stream_reports'][0]
        assert prior['status'] == 'PARTIAL' and prior['summary_meta']['reason'] == 'RECONCILIATION_INTERRUPTED'
    with c.uow() as repo:
        assert repo.session.get(SyncReconciliationRun, old_id).status == 'RUNNING'  # GET never writes.
    c.reconciliation_jobs.start()
    finished(c)
    assert visited == [ids[1], ids[0]]
    with c.uow() as repo:
        assert repo.session.get(SyncReconciliationRun, old_id).status == 'PARTIAL'
    c.reconciliation_jobs.close()


def test_file_budget_stops_cleanly_and_releases_metadata_connection_before_io(source_viewer, monkeypatch):
    from threading import get_ident

    from sqlalchemy import event

    c, version, _, _ = source_viewer
    with c.uow() as repo:
        values = {k: v for k, v in repo.get('resource_versions', version['id']).items()
                  if k not in ('id', 'created_at', 'updated_at')}
        values['version_number'] += 1
        repo.insert('resource_versions', values)
    c.settings.reconciliation_max_files_per_run = 1
    visited = []
    owners = {}
    def checkout(connection, record, proxy):
        owners[id(connection)] = get_ident()
    def checkin(connection, record):
        owners.pop(id(connection), None)
    event.listen(c.engine, 'checkout', checkout)
    event.listen(c.engine, 'checkin', checkin)
    def verify(versions, origins, *, audit_type):
        # The status poll may hold its own connection concurrently. The I/O
        # thread itself must have released the metadata connection.
        assert get_ident() not in tuple(owners.values())
        assert audit_type == 'LIGHT'
        visited.extend(row.id for row in versions)
        return [{'discrepancy_flag': 'NOT_VERIFIED', 'target_value': {'reason_code': 'SIZE_ONLY'}}]
    monkeypatch.setattr(c.reconciliation_engine, 'verify_file_snapshot', verify)
    try:
        c.reconciliation_jobs.start()
        run = finished(c)
        assert run.status == 'PARTIAL'
        assert len(visited) == 1
        assert run.summary_meta['processed_files'] == 1
        assert run.summary_meta['reason'] == 'RECONCILIATION_BUDGET'
        assert run.summary_meta['last_processed_id'] == visited[0]
    finally:
        c.reconciliation_jobs.close()
        event.remove(c.engine, 'checkout', checkout)
        event.remove(c.engine, 'checkin', checkin)
