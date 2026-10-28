from sqlalchemy import select

from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.db.models import SyncReconciliationRun


def test_exact_repair_does_not_move_global_cursor_or_scan_stream(container, monkeypatch):
    container.sync.run(full=True)
    with container.uow() as repo:
        before = repo.get('sync_checkpoints', 'subjects')
        row = repo.find('subjects', limit=1)[0]
    reads = []
    original = container.source.selected_batches
    def selected(stream, ids, size):
        reads.extend(ids)
        yield from original(stream, ids, size)
    monkeypatch.setattr(container.source, 'selected_batches', selected)
    monkeypatch.setattr(container.source, 'batches', lambda *args: (_ for _ in ()).throw(AssertionError('full scan')))
    result = container.sync.run_selected('subjects', [row['id']])
    assert reads == [row['id']]
    assert result['records_read'] == 1
    with container.uow() as repo:
        assert repo.get('sync_checkpoints', 'subjects') == before
        assert repo.get('sync_runs', result['run_id'])['sync_mode'] == 'REPAIR'


def test_durable_schedules_are_independent_deduplicated_and_claimed_when_legacy_off(container):
    schedule = AuditSchedule(container.uow, container.settings)
    assert all(not row['enabled'] for row in schedule.list())
    schedule.configure('subjects', {'enabled': True, 'interval_seconds': 1200, 'mode': 'FULL', 'repair': True})
    schedule.configure('files', {'enabled': True, 'interval_seconds': 900, 'mode': 'INCREMENTAL', 'repair': True})
    schedule.tick()
    schedule.tick()
    with container.uow() as repo:
        jobs = list(repo.session.scalars(select(Job).where(Job.status == 'PENDING')))
        assert len(jobs) == 2
        assert {(r.audit_type, r.stream_name) for r in jobs} == {('METADATA', 'subjects'), ('LIGHT', 'files')}
        assert next(r for r in jobs if r.stream_name == 'subjects').checkpoint['repair_requested']
        assert not next(r for r in jobs if r.stream_name == 'files').checkpoint['repair_requested']
    claim = ScheduledAudit(container.reconciliation_engine, container.settings).queue.claim()
    assert claim is not None


def test_bounded_metadata_job_repairs_only_mismatch(container, monkeypatch):
    container.sync.run(full=True)
    with container.uow() as repo:
        row = repo.find('subjects', limit=1)[0]
        # Fixture rows have no source ownership fence, so simulate a stale target.
        repo.update('subjects', row['id'], {'subject_name': 'stale'})
    repaired = []
    original = container.sync.run_selected
    def repair(stream, ids, **kwargs):
        repaired.extend(ids)
        return original(stream, ids, **kwargs)
    monkeypatch.setattr(container.sync, 'run_selected', repair)
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, container.sync)
    job = audit.queue.enqueue('METADATA', 'subjects', mode='FULL', repair=True)
    audit.once()
    assert repaired == [row['id']]
    with container.uow() as repo:
        assert repo.session.get(Job, job).status == 'COMPLETED'
        assert repo.get('subjects', row['id'])['subject_name'] != 'stale'


def test_repair_cannot_claim_success_when_target_changes_after_write(container, monkeypatch):
    from data_sync_etl.db.models import SyncReconciliationDiscrepancy
    container.sync.run(full=True)
    with container.uow() as repo:
        row = repo.find('subjects', limit=1)[0]
        repo.update('subjects', row['id'], {'subject_name': 'stale'})
    original = container.sync.run_selected
    def repair(*args, **kwargs):
        result = original(*args, **kwargs)
        with container.uow() as repo:
            repo.update('subjects', row['id'], {'subject_name': 'changed-again'})
        return result
    monkeypatch.setattr(container.sync, 'run_selected', repair)
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, container.sync)
    job_id = audit.queue.enqueue('METADATA', 'subjects', mode='FULL', repair=True)
    audit.once(job_id)
    with container.uow() as repo:
        job = repo.session.get(Job, job_id)
        assert job.status == 'RETRY_WAIT'
        assert job.checkpoint['repair_pending'] == [row['id']]
        assert all(row.resolution_status == 'UNRESOLVED' for row in repo.session.scalars(
            select(SyncReconciliationDiscrepancy).where(SyncReconciliationDiscrepancy.run_id == job_id)))


def test_users_freshness_requires_complete_private_audit_not_partial_repair(container):
    from datetime import timedelta

    import pytest

    from data_sync_etl.db.models import SyncRun
    from data_sync_etl.domain.core import now
    from data_sync_etl.security.auth import AuthError
    from data_sync_etl.security.master_service import MasterIdentityReader

    container.sync.run(full=True)
    with container.uow() as repo:
        for row in repo.session.scalars(select(SyncRun).where(SyncRun.stream_name == 'users')):
            row.finished_at = now() - timedelta(hours=1)
    reader = MasterIdentityReader(container.settings, engine=container.engine)
    container.sync.run_selected('users', ['user-1'])
    with pytest.raises(AuthError, match='AUTH_MASTER_STALE'):
        reader.read(user_id='user-1')
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, container.sync)
    job_id = audit.queue.enqueue('METADATA', 'users', mode='FULL', repair=True)
    audit.once(job_id)
    with container.uow() as repo:
        row = repo.session.get(Job, job_id)
        assert row.status == 'COMPLETED', row.error_code
        assert row.checkpoint['auth_verified'] is True
        assert 'hashed_password' not in str(repo.session.get(SyncReconciliationRun, job_id).summary_meta)
    assert reader.read(user_id='user-1') is not None


def test_private_user_delta_includes_role_changes_without_exposing_password(tmp_path):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from sqlalchemy import create_engine, text

    from data_sync_etl.adapters.persistence.audit_metadata import metadata_pages
    from data_sync_etl.domain.reconciliation import serialize_json_value

    engine = create_engine('sqlite:///' + (tmp_path / 'private-user-source.db').as_posix())
    try:
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE roles(id TEXT,code TEXT,is_active BOOLEAN,deleted_at TEXT,updated_at TEXT)'))
            conn.execute(text('CREATE TABLE users(id TEXT,role_id TEXT,email TEXT,hashed_password TEXT,updated_at TEXT)'))
            conn.execute(text("INSERT INTO roles VALUES('r','USER',1,NULL,'2026-10-28T12:00:00+00:00')"))
            conn.execute(text("INSERT INTO users VALUES('u','r','fixture@example.test','fixture-private-hash','2026-10-01T12:00:00+00:00')"))
        pages = list(metadata_pages(SimpleNamespace(engine=engine), 'users',
            datetime(2026, 10, 27, tzinfo=UTC), None, 10))
        assert pages[0][0]['role_code'] == 'USER'
        assert pages[0][0]['deleted_at'] is None  # Canonical SOURCE users omits this column.
        assert pages[0][0]['hashed_password'] == 'fixture-private-hash'
        assert 'fixture-private-hash' not in str(serialize_json_value(pages))
    finally:
        engine.dispose()


def test_users_delta_does_not_assert_authoritative_auth_freshness(container):
    container.sync.run(full=True)
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, container.sync)
    first = audit.queue.enqueue('METADATA', 'users', mode='FULL', repair=True)
    audit.once(first)
    second = audit.queue.enqueue('METADATA', 'users', mode='INCREMENTAL', repair=True)
    audit.once(second)
    with container.uow() as repo:
        assert repo.session.get(Job, first).checkpoint['auth_verified'] is True
        delta = repo.session.get(Job, second)
        assert delta.status == 'COMPLETED'
        assert delta.checkpoint['since'] is not None
        assert not delta.checkpoint.get('auth_verified', False)


def test_targeted_repair_keeps_metadata_projection_outside_sync_lock(container):
    from types import SimpleNamespace
    container.sync.run(full=True)
    projected = []
    def project(stream):
        with container.uow.sync_lock() as acquired:
            assert acquired
        projected.append(stream)
        return {stream: {'status': 'COMPLETED'}}
    container.sync.metadata_projection = SimpleNamespace(run=project)
    container.sync.skip_shared_metadata_validation = False
    container.sync.run_selected('users', ['user-1'])
    assert projected == ['users']
    assert container.sync.last_metadata_report['users']['status'] == 'COMPLETED'


def test_source_disappearing_after_repair_is_not_marked_synced(container, monkeypatch):
    container.sync.run(full=True)
    with container.uow() as repo:
        row = repo.find('subjects', limit=1)[0]
        repo.update('subjects', row['id'], {'subject_name': 'stale'})
    original = container.sync.run_selected
    def repair(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(container.sync.source, 'selected_batches', lambda *args: iter(()))
        return result
    monkeypatch.setattr(container.sync, 'run_selected', repair)
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, container.sync)
    job = audit.queue.enqueue('METADATA', 'subjects', mode='FULL', repair=True)
    audit.once(job)
    with container.uow() as repo:
        assert repo.session.get(Job, job).status == 'RETRY_WAIT'
        assert repo.session.get(Job, job).checkpoint['repair_pending'] == [row['id']]


def test_disable_schedule_cancels_only_its_pending_work(container):
    schedule = AuditSchedule(container.uow, container.settings)
    values = dict(enabled=True, interval_seconds=900, mode='FULL', repair=True)
    schedule.configure('subjects', values)
    schedule.tick()
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    manual = audit.queue.enqueue('METADATA', 'subjects')
    schedule.configure('subjects', {**values, 'enabled': False})
    with container.uow() as repo:
        assert repo.session.get(Job, manual).status == 'PENDING'
        scheduled = repo.session.scalar(select(Job).where(Job.job_key.startswith('configured:subjects:')))
        assert scheduled.status == 'CANCELLED'


def test_configured_and_fallback_share_decisions_across_slots(container, monkeypatch):
    from datetime import timedelta
    from data_sync_etl.domain.core import now
    container.settings.reconciliation_scheduler_enabled = True
    schedule = AuditSchedule(container.uow, container.settings)
    schedule.configure('subjects', dict(enabled=True, interval_seconds=60, mode='FULL', repair=True))
    first = set(schedule.tick())
    monkeypatch.setattr('data_sync_etl.adapters.persistence.audit_schedule.now', lambda: stamp)
    stamp = now() + timedelta(days=2)
    assert set(schedule.tick()) == first
    with container.uow() as repo:
        jobs = list(repo.session.scalars(select(Job).where(Job.status == 'PENDING')))
        assert len(jobs) == 10
        subject = [row for row in jobs if row.stream_name == 'subjects']
        assert {row.audit_type for row in subject} == {'METADATA', 'IDS'}
        assert next(row for row in subject if row.audit_type == 'METADATA').checkpoint['repair_requested']
        assert {row.audit_type for row in jobs if row.stream_name == 'files'} == {'LIGHT', 'DEEP'}


def test_legacy_ticks_reuse_retrying_audit_instead_of_flooding_queue(container, monkeypatch):
    from datetime import timedelta

    from data_sync_etl.application.reconciled_sync import ReconciledSync
    from data_sync_etl.domain.core import now

    audit = ScheduledAudit(container.reconciliation_engine, container.settings, container.sync)
    job_id = audit.queue.enqueue('METADATA', 'subjects', mode='INCREMENTAL', repair=True)
    with container.uow() as repo:
        job = repo.session.get(Job, job_id)
        job.status, job.next_retry_at = 'RETRY_WAIT', now() + timedelta(minutes=5)
    service = ReconciledSync(container.reconciliation_engine, container.sync, container.settings)
    for _ in range(3):
        assert service.run('subjects')['status'] == 'RETRY_WAIT'
    with container.uow() as repo:
        assert len(list(repo.session.scalars(select(Job)))) == 1
