import pytest

from scripts.benchmark_search_sessions import run, summary


def test_small_bm25_fixture_preserves_evidence_and_reduces_rank_work():
    result = run(20)
    assert result['evidence_identical'] and result['independent_qrels'] is False
    assert result['before']['calls'] == {'rank': 10, 'hydrate': 10}
    assert result['after']['calls'] == {'rank': 2, 'hydrate': 10}
    assert result['after']['session_accounted_bytes'] <= 1024 * 1024


def test_benchmark_refuses_large_corpus_and_labels_small_samples():
    with pytest.raises(ValueError):
        run(10000)
    assert summary([1., 2., 3., 4., 5.]) == {'samples': 5, 'p50_ms': 3., 'p95_ms': 5.}
