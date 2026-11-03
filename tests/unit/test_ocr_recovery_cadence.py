from types import SimpleNamespace

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
