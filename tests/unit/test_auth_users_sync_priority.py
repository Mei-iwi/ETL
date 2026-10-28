"""Deterministic scheduling; no DB, no relaxed auth-freshness policy."""
from types import SimpleNamespace

import pytest

from data_sync_etl.workers.master_sync import MasterSyncWorker


@pytest.mark.parametrize('budget,streams,expected', [
    (120, ['resources', 'users', 'grades', 'subjects'], ['users', 'resources', 'users', 'grades', 'subjects']),
    (None, ['resources', 'users', 'grades'], ['grades', 'users', 'resources']),
    (120, ['resources', 'grades'], ['grades', 'resources']),
    (120, ['users'], ['users']),
])
def test_users_refresh_does_not_wait_for_the_whole_slow_cycle(monkeypatch, budget, streams, expected):
    clock, calls, completed = [0], [], []
    def run(stream, **kwargs):
        calls.append(stream)
        clock[0] += {'resources': 74, 'users': 11, 'grades': 7, 'subjects': 7}[stream]
    monkeypatch.setattr('data_sync_etl.workers.master_sync.monotonic', lambda: clock[0])
    coordinator = SimpleNamespace(heartbeat_and_claim=lambda _: streams, mode=lambda _: 'INCREMENTAL',
        worker_stale_seconds=30, lease_seconds=120, renew=lambda _: True,
        complete=lambda *args: completed.append(args))
    worker = MasterSyncWorker(SimpleNamespace(run=run), coordinator, auth_freshness_seconds=budget)
    assert worker.once('fixture') == {'action': 'COMPLETED'}
    assert calls == expected and completed == [('fixture', None)]


def test_failed_users_sync_never_fakes_freshness_or_retries_without_bound(monkeypatch):
    calls, completed = [], []
    def run(stream, **kwargs):
        calls.append(stream)
        if stream == 'users':
            raise RuntimeError('Synthetic SOURCE unavailable')
    coordinator = SimpleNamespace(heartbeat_and_claim=lambda _: ['resources', 'users', 'subjects'],
        mode=lambda _: 'INCREMENTAL', worker_stale_seconds=30, lease_seconds=120,
        renew=lambda _: True, complete=lambda *args: completed.append(args))
    worker = MasterSyncWorker(SimpleNamespace(run=run), coordinator, auth_freshness_seconds=120)
    assert worker.once('fixture') == {'action': 'FAILED'}
    assert calls == ['users', 'resources', 'subjects'] and completed == [('fixture', 'SYNC_FAILED')]


def test_composed_auto_worker_refreshes_real_users_sync_even_without_delta(container):
    from datetime import timedelta
    from sqlalchemy import select
    from data_sync_etl.db.models import SyncRun
    from data_sync_etl.domain.core import now
    from data_sync_etl.security.master_service import MasterIdentityReader
    container.sync.run(full=True)
    with container.uow() as repo:
        for run in repo.session.scalars(select(SyncRun).where(SyncRun.stream_name == 'users')):
            run.finished_at = now() - timedelta(hours=1)
        before = repo.get('sync_checkpoints', 'users')
    worker = container.master_sync_worker
    assert worker.sync is container.sync
    worker.metadata_refresh = None
    worker.auth_freshness_seconds = 120
    worker.coordinator = SimpleNamespace(heartbeat_and_claim=lambda _: ['users'],
        mode=lambda _: 'INCREMENTAL', worker_stale_seconds=30, lease_seconds=120,
        renew=lambda _: True, complete=lambda *args: None)
    assert worker.once('isolated-auth-test') == {'action': 'COMPLETED'}
    assert MasterIdentityReader(container.settings, engine=container.engine).read(user_id='user-1') is not None
    with container.uow() as repo:
        assert repo.get('sync_checkpoints', 'users') == before
        latest = repo.find('sync_runs', {'stream_name': 'users'}, order=('-started_at',), limit=1)[0]
        assert latest['sync_mode'] == 'INCREMENTAL' and latest['status'] == 'COMPLETED'
        assert latest['records_read'] == 0
