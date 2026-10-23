from concurrent.futures import ThreadPoolExecutor

import pytest

from data_sync_etl.domain import lexical


def test_analysis_reuses_only_exact_text_and_copies_results(monkeypatch):
    calls = []
    monkeypatch.setattr(lexical, '_analyze', lambda text: (calls.append(text), [text])[-1])
    with lexical.analysis_scope():
        lexical.analyze('first').append('changed')
        assert lexical.analyze('first') == ['first']
        assert lexical.analyze('updated') == ['updated']
        with lexical.analysis_scope():
            assert lexical.analyze('first') == ['first']
    assert calls == ['first', 'updated']
    with lexical.analysis_scope():
        lexical.analyze('first')
    assert calls == ['first', 'updated', 'first']


def test_failed_analysis_is_never_cached_and_scope_is_released(monkeypatch):
    calls = []
    def fail(text):
        calls.append(text)
        raise RuntimeError('unavailable')
    monkeypatch.setattr(lexical, '_analyze', fail)
    with pytest.raises(RuntimeError), lexical.analysis_scope():
        lexical.analyze('text')
    assert lexical._ANALYSIS.get() is None
    with pytest.raises(RuntimeError), lexical.analysis_scope():
        lexical.analyze('text')
    assert calls == ['text', 'text']


def test_analysis_retention_is_bounded_and_threads_are_isolated(monkeypatch):
    monkeypatch.setattr(lexical, '_analyze', lambda text: [text])
    with lexical.analysis_scope():
        lexical.analyze('small')
        lexical.analyze('x' * 2_000_001)
        assert list(lexical._ANALYSIS.get()['tokens']) == ['small']
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(lexical._ANALYSIS.get).result() is None
