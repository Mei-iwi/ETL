"""Deterministic scheduling; no DB, no relaxed auth-freshness policy."""
from types import SimpleNamespace

import pytest

from data_sync_etl.workers.master_sync import MasterSyncWorker


@pytest.mark.parametrize('budget,streams,expected', [
    (120, ['resources', 'users', 'grades', 'subjects'], ['users', 'resources', 'users', 'grades', 'subjects']),
    (None, ['resources', 'users', 'grades'], ['resources', 'users', 'grades']),
    (120, ['resources', 'grades'], ['resources', 'grades']),
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
