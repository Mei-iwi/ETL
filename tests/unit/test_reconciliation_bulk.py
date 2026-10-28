import pytest
from sqlalchemy import select

from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


@pytest.mark.parametrize('seconds', [60, 300, 600])
def test_common_short_schedule_allows_individual_override(container, seconds):
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        common = dict(enabled=True, interval_seconds=seconds, mode='INCREMENTAL', repair=True)
        assert client.put('/api/v1/reconciliation/schedules/all', json=common).status_code == 200
        plans = client.get('/api/v1/reconciliation/schedules').json()['items']
        assert len(plans) == 5
        assert all(plan['enabled'] and plan['interval_seconds'] == seconds for plan in plans)
        assert all(plan['repair'] == (plan['stream'] != 'files') for plan in plans)
        assert client.put('/api/v1/reconciliation/schedules/users',
            json={**common, 'enabled': False, 'interval_seconds': 3600}).status_code == 200
        plans = client.get('/api/v1/reconciliation/schedules').json()['items']
        assert next(plan for plan in plans if plan['stream'] == 'users')['enabled'] is False
        assert all(plan['enabled'] and plan['interval_seconds'] == seconds for plan in plans if plan['stream'] != 'users')


@pytest.mark.parametrize('mode,kind', [('INCREMENTAL', 'LIGHT'), ('FULL', 'DEEP')])
def test_common_manual_scan_creates_separate_jobs_without_changing_schedule(container, mode, kind):
    container.settings.admin_enabled = True
    schedule = AuditSchedule(container.uow, container.settings)
    before = schedule.list()
    with TestClient(create_app(container)) as client:
        result = client.post('/api/v1/reconciliation/audit-jobs/all', json={'mode': mode, 'repair': True})
    assert result.status_code == 202
    assert len({row['job_id'] for row in result.json()['items']}) == 5
    with container.uow() as repo:
        jobs = list(repo.session.scalars(select(Job)))
        assert len(jobs) == 5
        assert all(job.status == 'PENDING' and job.checkpoint['mode'] == mode for job in jobs)
        assert all(job.audit_type == (kind if job.stream_name == 'files' else 'METADATA') for job in jobs)
        assert all(job.checkpoint['repair_requested'] == (job.stream_name != 'files') for job in jobs)
    assert schedule.list() == before


def test_common_actions_require_admin_and_reject_unsupported_intervals(container):
    with TestClient(create_app(container)) as client:
        assert client.put('/api/v1/reconciliation/schedules/all', json={}).status_code == 403
        assert client.post('/api/v1/reconciliation/audit-jobs/all', json={}).status_code == 403
        container.settings.admin_enabled = True
        for seconds in (0, 1, 59, 61, 120):
            assert client.put('/api/v1/reconciliation/schedules/all', json={'interval_seconds': seconds}).status_code == 422
        assert client.post('/api/v1/reconciliation/audit-jobs/all', json={'mode': 'OTHER'}).status_code == 422


def test_common_queue_is_atomic_if_one_job_cannot_be_created(container, monkeypatch):
    queue = AuditQueue(container.uow, container.settings)
    original = queue._enqueue_in
    def fail(repo, kind, stream, *args):
        if stream == 'users':
            raise RuntimeError('isolated third-job failure')
        return original(repo, kind, stream, *args)
    monkeypatch.setattr(queue, '_enqueue_in', fail)
    with pytest.raises(RuntimeError):
        queue.enqueue_all()
    with container.uow() as repo:
        assert list(repo.session.scalars(select(Job))) == []


def test_common_schedule_is_atomic_if_one_row_cannot_be_created(container, monkeypatch):
    import data_sync_etl.adapters.persistence.audit_schedule as module
    original, calls = module.new_id, []
    def fail():
        calls.append(True)
        if len(calls) == 3:
            raise RuntimeError('isolated third-schedule failure')
        return original()
    monkeypatch.setattr(module, 'new_id', fail)
    with pytest.raises(RuntimeError):
        AuditSchedule(container.uow, container.settings).configure('all',
            dict(enabled=True, interval_seconds=60, mode='FULL', repair=True))
    with container.uow() as repo:
        assert list(repo.session.scalars(select(Job))) == []
