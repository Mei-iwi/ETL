from types import SimpleNamespace

import pytest

from data_sync_etl.domain.core import DomainError
from data_sync_etl.workers import ocr


def test_slow_recovery_waits_a_full_interval_before_the_next_recovery(monkeypatch):
    clock = [0.0]
    calls = []

    def recover():
        calls.append(clock[0])
        clock[0] += 45
        return {'recovered': 0}

    monkeypatch.setattr(ocr, 'monotonic', lambda: clock[0])
    pipeline = SimpleNamespace(recover_stale=recover, claim=lambda _worker: None)
    worker = ocr.OCRWorker(pipeline, recovery_interval_seconds=30)
    assert not worker.once('test')
    assert not worker.once('test')
    assert calls == [0]
    clock[0] = 75
    assert not worker.once('test')
    assert calls == [0, 75]


def test_heartbeat_retries_connection_failure_then_stops_on_lost_owner():
    outcomes = iter([OSError('connection unavailable'), True, False])
    intervals = []

    def heartbeat(_task):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    stop = SimpleNamespace(wait=lambda seconds: intervals.append(seconds) or False)
    worker = ocr.OCRWorker(SimpleNamespace(heartbeat_timeout=120, heartbeat=heartbeat))
    worker._heartbeat({'id': 'task'}, stop)
    assert intervals == [40, 5, 40]


@pytest.mark.parametrize('code', ['SOURCE_OCR_LEASE_LOST', 'SOURCE_OCR_UNAUTHORIZED',
                                 'SOURCE_VERSION_MUTATED', 'SOURCE_OCR_CONTRACT_DENIED'])
def test_heartbeat_does_not_retry_lost_lease_or_revoked_authority(code):
    attempts = []

    def heartbeat(_task):
        attempts.append(True)
        raise DomainError(code)

    worker = ocr.OCRWorker(SimpleNamespace(heartbeat_timeout=120, heartbeat=heartbeat))
    worker._heartbeat({'id': 'task'}, SimpleNamespace(wait=lambda _: False))
    assert len(attempts) == 1


def test_source_worker_gives_finalization_a_turn_after_each_page():
    calls = []
    pipeline = SimpleNamespace(source_owned=True, heartbeat_timeout=120,
        recover_stale=lambda: {'recovered': 0},
        claim=lambda _: calls.append('claim') or {'id': 't', 'job_id': 'j', 'source_job_id': 'sj'},
        recognize=lambda _: (None, None), succeed=lambda *args: None)
    worker = ocr.OCRWorker(pipeline, reconcile=lambda: calls.append('finalize/index') or True)
    assert worker.once('test')
    assert worker.once('test')
    assert calls == ['claim', 'finalize/index']
