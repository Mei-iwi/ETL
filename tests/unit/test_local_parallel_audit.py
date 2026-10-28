"""LOCAL-only real file reads; detached threads, coordinator-only SQLite writes."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock, get_ident
from time import perf_counter, sleep

import pytest
from sqlalchemy import func, select

from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.db.models import ReconciliationFileState as State
from data_sync_etl.domain.core import DomainError, now
from tests.unit.test_reconciliation_light_deep import local_file as local_file


def pair(fixture):
    c, path, first = fixture
    second_path = path.with_name('second.pdf')
    second_path.write_bytes(b'second!!')
    with c.uow() as repo:
        values = repo.get('resource_versions', first)
        repo.insert('resource_versions', {**values, 'id': 'z-second', 'version_number': 2,
            'storage_object_key': second_path.name, 'source_hash': hashlib.sha256(b'second!!').hexdigest()})
    c.settings.reconciliation_file_concurrency = 2
    return c, first, 'z-second'


def test_one_deep_job_overlaps_two_files_and_commits_contiguous_order(local_file, monkeypatch):
    c, first, second = pair(local_file)
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    barrier, second_done = Barrier(2), Event()
    completed, saved = [], []
    coordinator = get_ident()
    original_hash = type(c.reconciliation_engine)._hash_local
    original_save = audit._save
    def read(engine, path, deadline):
        barrier.wait(timeout=3)
        if path.name == 'file.pdf':
            assert second_done.wait(3)
        result = original_hash(engine, path, deadline)
        completed.append(path.name)
        if path.name == 'second.pdf':
            second_done.set()
        return result
    def save(claim, checkpoint, results=(), file_state=None, status=None):
        assert get_ident() == coordinator
        if file_state:
            saved.append(file_state[0])
        return original_save(claim, checkpoint, results, file_state, status)
    with monkeypatch.context() as patch:
        patch.setattr(type(c.reconciliation_engine), '_hash_local', read)
        patch.setattr(audit, '_save', save)
        assert audit.once(key)
    assert completed == ['second.pdf', 'file.pdf'] and saved == [first, second]
    with c.uow() as repo:
        checkpoint = repo.session.get(Job, key).checkpoint
        assert checkpoint['io_concurrency'] == 2 and checkpoint['bytes_read'] == 16
        assert checkpoint['records_scanned'] == checkpoint['sha256_computed_count'] == 2
    assert audit.coverage()['recent_sha256_evidence_count'] == 2
    light = audit.queue.enqueue('LIGHT', 'files')
    assert audit.once(light)
    assert audit.coverage()['recent_sha256_evidence_count'] == 2
    local_file[1].with_name('second.pdf').write_bytes(b'corrupt!')
    assert audit.once(audit.queue.enqueue('LIGHT', 'files'))
    assert audit.coverage()['recent_sha256_evidence_count'] == 1
    assert audit.once(audit.queue.enqueue('DEEP', 'files', mode='FULL'))
    with c.uow() as repo:
        assert repo.session.get(State, second).status == 'DESYNC_CHECKSUM'
    assert audit.coverage()['recent_sha256_evidence_count'] == 1


def test_crash_before_first_commit_replays_uncommitted_second_file(local_file, monkeypatch):
    c, _, _ = pair(local_file)
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    original = audit._save
    def crash(claim, checkpoint, results=(), file_state=None, status=None):
        if file_state:
            raise DomainError('FIXTURE_CRASH_BEFORE_PREFIX_COMMIT')
        return original(claim, checkpoint, results, file_state, status)
    with monkeypatch.context() as patch:
        patch.setattr(audit, '_save', crash)
        assert audit.once(key)
    with c.uow() as repo:
        job = repo.session.get(Job, key)
        assert job.checkpoint.get('last_id') is None
        assert job.checkpoint['records_scanned'] == 0
        assert repo.session.scalar(select(func.count()).select_from(State)) == 0
        job.next_retry_at = now() - timedelta(seconds=1)
    assert audit.once(key)
    assert audit.coverage()['recent_sha256_evidence_count'] == 2


def test_takeover_during_reads_discards_both_late_results(local_file, monkeypatch):
    c, _, _ = pair(local_file)
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    entered, release, barrier = Event(), Event(), Barrier(2)
    original_hash = type(c.reconciliation_engine)._hash_local
    def read(engine, path, deadline):
        barrier.wait(timeout=3)
        entered.set()
        assert release.wait(3)
        return original_hash(engine, path, deadline)
    monkeypatch.setattr(type(c.reconciliation_engine), '_hash_local', read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(audit.once, key)
        assert entered.wait(3)
        with c.uow() as repo:
            job = repo.session.get(Job, key)
            job.claim_token = 'replacement-owner'
            job.lease_expires_at = now() + timedelta(seconds=60)
        release.set()
        assert future.result(timeout=5) is True  # Old-owner failure is deliberately swallowed by queue.fail.
    with c.uow() as repo:
        job = repo.session.get(Job, key)
        assert job.claim_token == 'replacement-owner' and job.status == 'RUNNING'
        assert job.checkpoint['records_scanned'] == 0
        assert repo.session.scalar(select(func.count()).select_from(State)) == 0
    assert audit.coverage()['recent_sha256_evidence_count'] == 0


def test_parallel_opt_in_does_not_multiply_byte_budget(local_file):
    c, _, _ = pair(local_file)
    c.settings.reconciliation_max_bytes_per_run = 8
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    assert audit.once(key)
    with c.uow() as repo:
        row = repo.session.get(Job, key)
        assert row.status == 'PARTIAL' and row.checkpoint['bytes_read'] <= 8
        assert row.checkpoint['records_scanned'] == 1
    assert audit.coverage()['recent_sha256_evidence_count'] == 1


def test_multiple_pairs_share_one_detached_db_page_and_one_budget(local_file, monkeypatch):
    c, first, _ = pair(local_file)
    with c.uow() as repo:
        values = repo.get('resource_versions', first)
        for number in (3, 4):
            name = f'file-{number}.pdf'
            local_file[1].with_name(name).write_bytes(b'original')
            repo.insert('resource_versions', {**values, 'id': f'zz-{number}', 'version_number': number,
                'storage_object_key': name})
    original = type(c.reconciliation_engine)._hash_local
    barrier = Barrier(2)
    def read(engine, path, deadline):
        barrier.wait(timeout=3)
        return original(engine, path, deadline)
    monkeypatch.setattr(type(c.reconciliation_engine), '_hash_local', read)
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    assert audit.once(key)
    assert audit.coverage()['recent_sha256_evidence_count'] == 4
    with c.uow() as repo:
        assert repo.session.get(Job, key).checkpoint['bytes_read'] == 32


def test_file_changed_while_waiting_for_prefix_commit_cannot_publish_sha(local_file, monkeypatch):
    c, first, second = pair(local_file)
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    original = audit._save
    def save(claim, checkpoint, results=(), file_state=None, status=None):
        result = original(claim, checkpoint, results, file_state, status)
        if file_state and file_state[0] == first:
            local_file[1].with_name('second.pdf').write_bytes(b'corrupt!')
        return result
    monkeypatch.setattr(audit, '_save', save)
    assert audit.once(key)
    with c.uow() as repo:
        state = repo.session.get(State, second)
        assert state.status == 'NOT_VERIFIED' and state.reason_code == 'LOCAL_FILE_CHANGED'
        assert state.last_verified_at is None
        assert repo.session.get(Job, key).checkpoint['bytes_read'] == 16
    assert audit.coverage()['recent_sha256_evidence_count'] == 1


def test_source_provider_is_not_fanned_out_and_invalid_limit_is_rejected(local_file):
    from types import SimpleNamespace

    from pydantic import ValidationError

    from data_sync_etl.config import Settings
    c, first, second = pair(local_file)
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    with c.uow() as repo:
        page = [(SimpleNamespace(**repo.get('resource_versions', key)), 'POSTGRESQL', None) for key in (first, second)]
    assert audit._local_pair(page, c.reconciliation_engine,
        {'audit_type': 'DEEP', 'checkpoint': {'mode': 'FULL'}}, Event(), 100, 2, perf_counter() + 1) == {}
    assert Settings(_env_file=None).reconciliation_file_concurrency == 1
    for value in (0, 3):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, reconciliation_file_concurrency=value)


def test_local_deep_fixture_benchmark(local_file, monkeypatch, capsys):
    c, _, _ = pair(local_file)
    original = type(c.reconciliation_engine)._hash_local
    meter = Lock()
    active, peak = 0, 0
    def read(engine, path, deadline):
        nonlocal active, peak
        with meter:
            active += 1
            peak = max(peak, active)
        try:
            sleep(.02)  # deterministic fake I/O latency, actual SHA checks follow
            return original(engine, path, deadline)
        finally:
            with meter:
                active -= 1
    monkeypatch.setattr(type(c.reconciliation_engine), '_hash_local', read)
    reports = []
    for concurrency in (1, 2):
        peak = 0
        c.settings.reconciliation_file_concurrency = concurrency
        audit = ScheduledAudit(c.reconciliation_engine, c.settings)
        key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
        start = perf_counter()
        assert audit.once(key)
        reports.append({'concurrency': concurrency, 'files': 2, 'bytes': 16,
            'wall_seconds': round(perf_counter() - start, 4), 'peak_in_flight': peak,
            'measurement': 'SQLITE_LOCAL_SHA_WITH_FAKE_20MS_IO_NOT_PRODUCTION'})
    with capsys.disabled():
        print('BOUNDED_DEEP_FIXTURE=' + json.dumps(reports))
