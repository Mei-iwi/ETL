"""Control-flow/privacy tests, not evidence of physical DB query counts."""

import json

import pytest

from tests.search_read_path_probe import ACTIVE, Probe, Trace, shape, summary


def test_shapes_and_hmac_do_not_retain_payload():
    probe = Probe()
    payload = {'filter': {'text': 'private content'}, 'limit': 9, 'lsid': {'id': 'secret'}}
    assert 'private content' not in json.dumps(shape(payload))
    assert 'secret' not in json.dumps(shape(payload))
    assert probe.key(payload) == probe.key(payload)
    assert probe.key(payload) != Probe().key(payload)
    assert len(probe.key(payload)) == 64


@pytest.mark.parametrize('enabled', [False, True])
def test_wrapper_parity_and_context_reset(enabled):
    probe = Probe()
    trace = Trace('isolated', enabled)
    expected = {'not_retained': 'payload'}
    def operation():
        return expected
    token = ACTIVE.set(trace)
    try:
        wrapped = probe.wrap(operation, 'logical', logical=True, returned=True)
        assert wrapped() is expected
        assert trace.returned_at is not None
        assert len(trace.logical) == int(enabled)
        assert not trace.stack
        assert 'payload' not in json.dumps(trace.logical)
    finally:
        ACTIVE.reset(token)
    assert ACTIVE.get() is None


def test_failure_releases_instrumentation_stack():
    probe, trace = Probe(), Trace('failure', True)
    def failed():
        raise ValueError('private exception')
    token = ACTIVE.set(trace)
    try:
        with pytest.raises(ValueError):
            probe.wrap(failed, 'failure', logical=True)()
        assert not trace.stack and trace.logical[0]['failed']
        assert 'private exception' not in json.dumps(trace.logical)
    finally:
        ACTIVE.reset(token)


def test_summary_reports_sample_size_and_dispersion():
    assert summary([]) == {'n': 0}
    result = summary([1., 2., 3., 4., 5.])
    assert result['n'] == 5 and result['p50'] == 3. and result['p95'] == 4.8
    assert result['min'] == 1. and result['max'] == 5. and result['stddev'] > 0
