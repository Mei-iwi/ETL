"""Checkpoint privacy and fail-closed diagnostics; no external service calls."""

import json

import httpx
import pytest

from tests.search_t1_support import FailureCheckpoint, UnexpectedSearch5xx


@pytest.mark.parametrize('body', [None, {'detail': 'private token'}, {'detail': {'code': 'private token'}}])
def test_unknown_503_is_persisted_without_payload_before_fail(tmp_path, body):
    target = tmp_path / 'errors.json'
    recorder = FailureCheckpoint(target)
    with pytest.raises(UnexpectedSearch5xx):
        recorder.observe(httpx.Response(503, json=body), method='vector', request_id=1,
                         duration_ms=2., fixture='unit')
    saved = target.read_text()
    assert 'private token' not in saved
    assert json.loads(saved)['events'][0]['code'] == 'UNCLASSIFIED_5XX'


def test_busy_and_injected_timeout_are_distinct(tmp_path):
    recorder = FailureCheckpoint(tmp_path / 'errors.json')
    for code, injected in [('SEARCH_RUNTIME_BUSY', False), ('SEARCH_TIMEOUT', True)]:
        recorder.observe(httpx.Response(503, json={'detail': {'code': code}}), method='hybrid',
            request_id=len(recorder.events) + 1, duration_ms=1., fixture='unit', simulated=injected, expected=code)
    assert recorder.events[0]['phase'] == 'admission'
    assert recorder.events[1]['phase'] == 'deadline'
    assert all(v['expected'] for v in recorder.events)


def test_real_timeout_closes_gate_even_if_caller_expects_it(tmp_path):
    recorder = FailureCheckpoint(tmp_path / 'errors.json')
    with pytest.raises(UnexpectedSearch5xx):
        recorder.observe(httpx.Response(503, json={'detail': {'code': 'SEARCH_TIMEOUT'}}),
            method='bow', request_id=1, duration_ms=1., fixture='unit', expected='SEARCH_TIMEOUT')
    assert recorder.events[0]['expected'] is False


@pytest.mark.parametrize('fail', [False, True])
def test_awake_guard_restores_previous_state_on_exit(fail):
    from tests.search_t1_support import awake_test_host

    calls = []
    def state(value):
        calls.append(value)
        return 0x80000002  # Existing display requirement belongs to the caller.
    def run():
        with awake_test_host(state) as enabled:
            assert enabled and calls == [0x80000001]
            if fail:
                raise ValueError('synthetic fixture failure')
    if fail:
        with pytest.raises(ValueError, match='synthetic fixture failure'):
            run()
    else:
        run()
    assert calls == [0x80000001, 0x80000002]


def test_awake_guard_fail_closed_if_system_request_fails():
    from tests.search_t1_support import awake_test_host

    with pytest.raises(RuntimeError, match='controlled awake'):
        with awake_test_host(lambda _: 0):
            raise AssertionError('Must not start benchmark without awake guard')
