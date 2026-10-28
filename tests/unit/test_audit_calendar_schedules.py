from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.domain.audit_calendar import schedule_slot, suggested_schedule
from tests.unit.test_reconciliation_light_deep import local_file as local_file


def plan(cadence='DAILY', **values):
    return {**suggested_schedule('subjects', 'fast'), 'enabled': True, 'cadence': cadence,
            'hour': 9, 'minute': 30, **values}


@pytest.mark.parametrize('cadence,values,before,at,next_stamp,period', [
    ('DAILY', {}, '2026-10-28T02:29:59', '2026-10-28T02:30:00', '2026-10-29T02:30:00', '2026-10-28'),
    ('WEEKLY', {'weekday': 2}, '2026-10-28T02:29:59', '2026-10-28T02:30:00', '2026-11-04T02:30:00', '2026-10-26'),
    ('MONTHLY', {'month_day': 28}, '2026-10-28T02:29:59', '2026-10-28T02:30:00', '2026-11-28T02:30:00', '2026-10'),
    ('MONTHLY', {'month_day': 1}, '2026-12-01T02:29:59', '2026-12-01T02:30:00', '2027-01-01T02:30:00', '2026-12'),
    ('MONTHLY', {'month_day': 28}, '2027-02-28T02:29:59', '2027-02-28T02:30:00', '2027-03-28T02:30:00', '2027-02'),
])
def test_calendar_slots_before_at_and_after_local_boundary(cadence, values, before, at, next_stamp, period):
    spec = plan(cadence, **values)
    stamp = lambda value: datetime.fromisoformat(value).replace(tzinfo=UTC)
    assert schedule_slot(spec, stamp(before)) == (None, stamp(at))
    assert schedule_slot(spec, stamp(at)) == (period, stamp(next_stamp))
    assert schedule_slot(spec, stamp(at) + timedelta(seconds=1)) == (period, stamp(next_stamp))


def test_week_and_month_never_enqueue_last_period_before_current_due():
    stamp = datetime(2026, 11, 2, 0, tzinfo=UTC)  # Monday, 07:00 local.
    assert schedule_slot(plan('WEEKLY', weekday=4), stamp)[0] is None
    assert schedule_slot(plan('MONTHLY', month_day=20), stamp)[0] is None


def test_timezone_and_dst_fold_have_one_period_key():
    spec = plan(hour=1, minute=30, timezone='America/New_York')
    first = schedule_slot(spec, datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    second = schedule_slot(spec, datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
    assert first == second
    assert first[0] == '2026-11-01'


def freeze(monkeypatch, stamp):
    monkeypatch.setattr('data_sync_etl.adapters.persistence.audit_schedule.now', lambda: stamp)


def test_both_branches_mapping_fallback_dedup_and_restart(container, monkeypatch):
    stamp = datetime(2026, 10, 28, 4, tzinfo=UTC)
    freeze(monkeypatch, stamp)
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('subjects', {'fast_schedule': plan(),
        'thorough_schedule': plan('WEEKLY', weekday=2, repair=True)})
    schedules.configure('files', {'fast_schedule': plan(),
        'thorough_schedule': plan('MONTHLY', month_day=28, repair=True)})
    first = schedules.tick()
    assert len(first) == 5  # Two metadata modes, their ID sweep, LIGHT and DEEP.
    assert schedules.tick() == first
    with container.uow() as repo:
        jobs = [repo.session.get(Job, key) for key in first]
        assert {(row.audit_type, row.stream_name, row.checkpoint['mode']) for row in jobs} == {
            ('METADATA', 'subjects', 'INCREMENTAL'), ('METADATA', 'subjects', 'FULL'),
            ('IDS', 'subjects', 'FULL'), ('LIGHT', 'files', 'INCREMENTAL'), ('DEEP', 'files', 'FULL')}
        assert not any(row.checkpoint['repair_requested'] for row in jobs if row.stream_name == 'files')
        for row in jobs:
            row.status = 'COMPLETED'
    assert set(AuditSchedule(container.uow, container.settings).tick()) == set(first)
    listed = {row['stream']: row for row in schedules.list()}
    assert listed['subjects']['fast_schedule']['next_run_at'] == '2026-10-29T02:30:00+00:00'
    assert listed['files']['thorough_schedule']['next_run_at'] == '2026-11-28T02:30:00+00:00'


def test_legacy_config_partial_put_and_independent_disable(container, monkeypatch):
    freeze(monkeypatch, datetime(2026, 10, 28, 4, tzinfo=UTC))
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('subjects', dict(enabled=True, interval_seconds=300, mode='FULL', repair=True))
    legacy = schedules.tick()[0]
    listed = next(row for row in schedules.list() if row['stream'] == 'subjects')
    assert listed['enabled'] and not listed['calendar_configured']
    schedules.configure('subjects', {'fast_schedule': plan(), 'thorough_schedule': plan('WEEKLY', weekday=2)})
    before = next(row for row in schedules.list() if row['stream'] == 'subjects')
    jobs = schedules.tick()
    manual = ScheduledAudit(container.reconciliation_engine, container.settings).queue.enqueue('METADATA', 'subjects')
    schedules.configure('subjects', {'thorough_schedule': {'enabled': False}})
    after = next(row for row in schedules.list() if row['stream'] == 'subjects')
    assert after['fast_schedule'] == before['fast_schedule']
    assert after['thorough_schedule']['weekday'] == 2
    with container.uow() as repo:
        assert repo.session.get(Job, legacy).status == 'CANCELLED'
        assert repo.session.get(Job, manual).status == 'PENDING'
        for key in jobs:
            row = repo.session.get(Job, key)
            assert row.status == ('CANCELLED' if ':thorough:' in row.job_key else 'PENDING')
        assert not list(repo.session.scalars(select(Job).where(Job.stream_name == 'users')))


def test_disabled_calendar_overrides_default_without_creating_audit_work(container):
    container.settings.reconciliation_scheduler_enabled = True
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('files', {'fast_schedule': {'enabled': False}, 'thorough_schedule': {'enabled': False}})
    schedules.tick()
    with container.uow() as repo:
        assert not list(repo.session.scalars(select(Job).where(Job.stream_name == 'files', Job.status != 'CONFIGURED')))


def test_calendar_deep_continues_same_cycle_through_bounded_jobs(local_file, monkeypatch):
    c, path, version = local_file
    stamp = datetime(2026, 10, 28, 4, tzinfo=UTC)
    freeze(monkeypatch, stamp)
    c.settings.reconciliation_max_files_per_run = 1
    schedules = AuditSchedule(c.uow, c.settings)
    schedules.configure('files', {'thorough_schedule': plan('MONTHLY', month_day=28)})
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    first = schedules.tick()[0]
    audit.once(first)
    with c.uow() as repo:
        row = repo.session.get(Job, first)
        assert row.status == 'PARTIAL' and row.checkpoint['last_id'] == version
        row.finished_at = stamp - timedelta(seconds=c.settings.reconciliation_retry_seconds + 1)
    # Manual and FAST jobs must not replace this cycle's cursor.
    audit.queue.enqueue('LIGHT', 'files')
    second = schedules.tick()[0]
    assert second != first
    assert schedules.tick() == [second]
    audit.once(second)
    with c.uow() as repo:
        row = repo.session.get(Job, second)
        assert row.status == 'COMPLETED'
        assert row.checkpoint['cycle_finished'] is True
        assert row.checkpoint['bytes_read'] == 0
        assert row.checkpoint['schedule_batch'] == 1
        assert row.checkpoint['schedule_cycle'].endswith('MONTHLY:2026-10')
        assert repo.count('ocr_jobs', {}) == 0
    assert schedules.tick() == [second]
    assert audit.coverage()['whole_repository_verified'] is False


def test_schedule_api_validates_and_preserves_the_other_branch(container):
    from data_sync_etl.main import create_app
    from tests.local_admin_client import TestClient
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        endpoint = '/api/v1/reconciliation/schedules/subjects'
        assert client.put(endpoint, json={'fast_schedule': plan(), 'thorough_schedule': plan('WEEKLY', weekday=2)}).status_code == 200
        assert client.put(endpoint, json={'thorough_schedule': {'enabled': False}}).status_code == 200
        data = next(row for row in client.get('/api/v1/reconciliation/schedules').json()['items'] if row['stream'] == 'subjects')
        assert data['fast_schedule']['enabled'] and data['thorough_schedule']['weekday'] == 2
        for value in [plan('MONTHLY', month_day=31), plan(weekday=7), plan(hour=24), plan(timezone='Invalid/Zone')]:
            assert client.put(endpoint, json={'fast_schedule': value}).status_code == 422


def test_incomplete_deep_proof_stays_partial_at_end_of_batched_cycle(local_file, monkeypatch):
    c, _, _ = local_file
    stamp = datetime(2026, 10, 28, 4, tzinfo=UTC)
    freeze(monkeypatch, stamp)
    c.settings.reconciliation_max_files_per_run = 1
    c.settings.reconciliation_max_bytes_per_run = 7
    schedules = AuditSchedule(c.uow, c.settings)
    schedules.configure('files', {'thorough_schedule': plan('MONTHLY', month_day=28)})
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    first = schedules.tick()[0]
    audit.once(first)
    with c.uow() as repo:
        row = repo.session.get(Job, first)
        assert row.checkpoint['not_verified_count'] == 1
        row.finished_at = stamp - timedelta(seconds=100)
    second = schedules.tick()[0]
    audit.once(second)
    with c.uow() as repo:
        row = repo.session.get(Job, second)
        assert row.status == 'PARTIAL' and row.checkpoint['cycle_finished']
        assert row.checkpoint['cycle_not_verified_count'] == 1
    assert schedules.tick() == [second]
    assert audit.coverage()['recent_sha256_evidence_count'] == 0


def test_metadata_full_continues_its_own_cursor_and_does_not_scan_other_streams(container, monkeypatch):
    container.sync.run(full=True)
    container.settings.reconciliation_max_files_per_run = 1
    stamp = datetime(2026, 10, 28, 4, tzinfo=UTC)
    freeze(monkeypatch, stamp)
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('subjects', {'thorough_schedule': plan('WEEKLY', weekday=2)})
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, container.sync)
    with container.uow() as repo:
        checkpoints = repo.find('sync_checkpoints')
        expected = repo.count('subjects', {})
    cycles = set()
    completed = False
    for _ in range(10):
        keys = schedules.tick()
        with container.uow() as repo:
            key = next(key for key in keys if repo.session.get(Job, key).audit_type == 'METADATA')
        audit.once(key)
        with container.uow() as repo:
            row = repo.session.get(Job, key)
            cycles.add(row.checkpoint['schedule_cycle'])
            if row.status == 'COMPLETED':
                assert row.checkpoint['records_scanned'] == expected
                completed = True
                break
            assert row.status == 'PARTIAL'
            row.finished_at = stamp - timedelta(seconds=100)
    assert completed and len(cycles) == 1
    with container.uow() as repo:
        assert repo.find('sync_checkpoints') == checkpoints


def test_two_ticks_share_the_scheduler_lock_and_do_not_enqueue_twice(container, monkeypatch):
    from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
    freeze(monkeypatch, datetime(2026, 10, 28, 4, tzinfo=UTC))
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('files', {'fast_schedule': plan()})
    original = AuditQueue.enqueue
    nested = []
    def enqueue(self, *args, **kwargs):
        nested.append(AuditSchedule(container.uow, container.settings).tick())
        return original(self, *args, **kwargs)
    monkeypatch.setattr(AuditQueue, 'enqueue', enqueue)
    assert len(schedules.tick()) == 1
    assert nested == [[]]
    with container.uow() as repo:
        assert len(list(repo.session.scalars(select(Job).where(Job.status == 'PENDING')))) == 1


def test_manual_job_has_priority_over_a_pending_calendar_batch(container, monkeypatch):
    freeze(monkeypatch, datetime(2026, 10, 28, 4, tzinfo=UTC))
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('files', {'fast_schedule': plan()})
    scheduled = schedules.tick()[0]
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    manual = audit.queue.enqueue('DEEP', 'files')
    assert audit.queue.claim()['id'] == manual
    with container.uow() as repo:
        assert repo.session.get(Job, scheduled).status == 'PENDING'


def test_turning_off_calendar_repair_cancels_queued_old_policy(container, monkeypatch):
    freeze(monkeypatch, datetime(2026, 10, 28, 4, tzinfo=UTC))
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('subjects', {'fast_schedule': plan(repair=True)})
    first = schedules.tick()[0]
    schedules.configure('subjects', {'fast_schedule': {'repair': False}})
    with container.uow() as repo:
        assert repo.session.get(Job, first).status == 'CANCELLED'
    freeze(monkeypatch, datetime(2026, 10, 29, 4, tzinfo=UTC))
    second = schedules.tick()[0]
    with container.uow() as repo:
        assert not repo.session.get(Job, second).checkpoint['repair_requested']


def test_old_paused_legacy_sweep_cannot_block_explicit_calendar(container, monkeypatch):
    freeze(monkeypatch, datetime(2026, 10, 28, 4, tzinfo=UTC))
    container.settings.reconciliation_scheduler_enabled = True
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    audit.schedule()
    with container.uow() as repo:
        old = repo.session.scalar(select(Job.id).where(Job.stream_name == 'subjects', Job.audit_type == 'IDS'))
    container.settings.reconciliation_scheduler_enabled = False
    schedules = AuditSchedule(container.uow, container.settings)
    schedules.configure('subjects', {'thorough_schedule': plan('WEEKLY', weekday=2)})
    jobs = schedules.tick()
    with container.uow() as repo:
        assert repo.session.get(Job, old).status == 'CANCELLED'
        assert len(jobs) == 2
        assert {repo.session.get(Job, key).audit_type for key in jobs} == {'METADATA', 'IDS'}
        assert all(repo.session.get(Job, key).job_key.startswith('configured:subjects:thorough:') for key in jobs)
