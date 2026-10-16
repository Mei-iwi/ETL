import math

import pytest

from data_sync_etl.domain.evaluation import aggregate, csv_cell, metrics, percentile


def test_hand_calculated_precision_recall_mrr_and_deduplication():
    result = metrics(['no', 'a', 'a', 'other'], {'a': 2, 'b': 3}, 5)
    assert result['precision'] == .2
    assert result['recall'] == .5
    assert result['mrr'] == .5
    assert result['ndcg'] == pytest.approx((3 / math.log2(3)) / (7 + 3 / math.log2(3)))


def test_undefined_is_not_zero_or_one():
    assert metrics(['x'], {'x': 0}, 5) == {'precision': 0., 'recall': None, 'mrr': None, 'ndcg': None}
    assert metrics([], {'a': 1}, 5) == {'precision': 0., 'recall': 0., 'mrr': 0., 'ndcg': 0.}


def test_macro_means_exclusions_and_interpolated_latency():
    rows = [{'method': 'bow', 'error_code': None, 'latency_ms': ms,
             'metrics': {'5': metrics(ranked, labels, 5)}}
            for ms, ranked, labels in [(10., ['a'], {'a': 1}), (30., [], {'x': 0})]]
    result = aggregate(rows, [5])['bow']
    assert result['metrics']['5']['precision']['value'] == .1
    assert result['metrics']['5']['recall'] == {'value': 1., 'valid_queries': 1, 'excluded_queries': 1}
    assert result['latency_ms'] == {'mean': 20., 'p50': 20., 'p95': 29.}
    assert percentile([], .95) is None


@pytest.mark.parametrize('value', ['=cmd', '+1', '-1', '@sum', ' \t=bad'])
def test_csv_formula_cells_are_escaped(value):
    assert csv_cell(value).startswith("'")
