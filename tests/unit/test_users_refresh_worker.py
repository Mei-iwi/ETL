from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data_sync_etl.domain.core import DomainError, SyncAlreadyRunning
from data_sync_etl.workers.users_refresh import UsersRefreshWorker


def setup(*, federation=True, **options):
    sync = SimpleNamespace(federation_enabled=federation, source_registry=Mock(),
                           run=Mock(return_value={'users': {'run_id': 'real-committed-run'}}))
    settings = SimpleNamespace(auth_mode='master_users', auth_master_freshness_seconds=120)
    return UsersRefreshWorker(sync, settings, source_id='source-a' if federation else None, **options), sync


def test_real_incremental_sync_uses_registry_and_not_subprocess_or_freshness_writes():
    worker, sync = setup()
    assert worker.interval_seconds == 40
    assert worker.run(once=True) == {'status': 'COMPLETED'}
    sync.source_registry.adapter_for_source_id.assert_called_once_with('source-a', 'read_metadata')
    sync.run.assert_called_once_with('users', full=False, source_id='source-a')


@pytest.mark.parametrize('interval', [0, -1, 61, True])
def test_interval_cannot_outlive_identity_freshness(interval):
    with pytest.raises(DomainError, match='USERS_REFRESH_INTERVAL_INVALID'):
        setup(interval_seconds=interval)


def test_busy_sync_and_secret_errors_are_safe_and_retry_next_turn(capsys):
    worker, sync = setup()
    sync.run.side_effect = [SyncAlreadyRunning('private details'), RuntimeError('secret DSN'),
                            {'users': {'run_id': 'recovered'}}]
    assert worker.once() == {'status': 'BUSY'}
    assert worker.once() == {'status': 'FAILED'}
    assert worker.once() == {'status': 'COMPLETED'}
    output = capsys.readouterr()
    assert 'secret' not in output.out + output.err and 'private details' not in output.out + output.err


@pytest.mark.parametrize('result', [{}, {'users': {'status': 'FAILED', 'run_id': 'failed'}},
                                   {'users': {'status': 'BLOCKED'}}])
def test_uncommitted_or_failed_results_do_not_report_success(result):
    worker, sync = setup()
    sync.run.return_value = result
    assert worker.once() == {'status': 'FAILED'}


def test_local_nonfederated_sync_has_no_source_id():
    worker, sync = setup(federation=False)
    assert worker.once()['status'] == 'COMPLETED'
    sync.run.assert_called_once_with('users', full=False)


def test_shutdown_wait_is_interruptible_and_uses_existing_lifecycle(monkeypatch):
    worker, sync = setup()
    stop = Mock()
    stop.is_set.side_effect = [False, True]
    @contextmanager
    def lifecycle():
        yield stop
    monkeypatch.setattr('data_sync_etl.workers.users_refresh.worker_lifecycle', lifecycle)
    monkeypatch.setattr('data_sync_etl.workers.users_refresh.tick', Mock())
    assert worker.run() == {'status': 'STOPPED'}
    stop.wait.assert_called_once_with(40)
    assert sync.run.call_count == 1


def test_missing_source_wrong_auth_or_registry_denial_fail_before_sync():
    settings = SimpleNamespace(auth_mode='master_users', auth_master_freshness_seconds=120)
    sync = SimpleNamespace(federation_enabled=True, source_registry=Mock(), run=Mock())
    with pytest.raises(DomainError, match='SOURCE_ID_REQUIRED'):
        UsersRefreshWorker(sync, settings)
    sync.source_registry.adapter_for_source_id.side_effect = DomainError('SOURCE_CAPABILITY_BLOCKED')
    with pytest.raises(DomainError, match='SOURCE_CAPABILITY_BLOCKED'):
        UsersRefreshWorker(sync, settings, source_id='unregistered')
    settings.auth_mode = 'legacy_local'
    with pytest.raises(DomainError, match='USERS_REFRESH_REQUIRES_MASTER_AUTH'):
        UsersRefreshWorker(sync, settings)
    sync.run.assert_not_called()
