import pytest

from scripts.benchmark_hybrid_snapshot import quality, summary


def test_answer_metrics_exclude_topical_only_and_use_fixed_precision_denominator():
    result = quality(['topic', 'answer'], {'topic': 1, 'answer': 3})
    assert result['precision1'] == 0.
    assert result['precision5'] == .2
    assert result['recall10'] == 1.
    assert result['mrr10'] == .5
    assert result['candidate_recall'] == 1.


def test_no_answer_false_positives_and_unsupported_evidence_are_reported_separately():
    rows = [{'resource': quality(['a'], {'a': 3}), 'chunk': None, 'snippet': None, 'ranking_ms': [2., 4.]},
            {'resource': quality(['topic'], {'topic': 1}), 'chunk': None, 'snippet': None, 'ranking_ms': [3.]}]
    report = summary(rows)
    assert report['resource']['precision1'] == 1.
    assert report['chunk']['precision1'] is None
    assert report['snippet']['precision1'] is None
    assert report['no_answer_fpr'] == 1.
    assert report['false_abstention_rate'] == 0.
    assert report['ranking_latency_ms']['p50'] == 3.
    assert report['ranking_latency_ms']['p95'] == pytest.approx(3.9)
