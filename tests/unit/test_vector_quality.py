import math

import pytest

from data_sync_etl.application.vector_quality import (
    calibrate_threshold,
    compile_qrels,
    diverse_candidates,
    evaluate_ranking,
    grouped_hits,
    summarize,
    validate_scores,
)


def test_chunk_and_resource_metrics_do_not_count_duplicate_resources_as_hits():
    chunks = {'a1': {'resource_id': 'a', 'version_id': 'v1'},
              'a2': {'resource_id': 'a', 'version_id': 'v1'},
              'b1': {'resource_id': 'b', 'version_id': 'v2'}}
    hits = grouped_hits({'a1': .9, 'a2': .89, 'b1': .8}, chunks)
    assert [hit['id'] for hit in hits] == ['a', 'b']
    assert evaluate_ranking(['a', 'b'], {'a': 3, 'b': 2})['recall5'] == 1.
    assert evaluate_ranking(['a1', 'a2'], {'b1': 3})['mrr10'] == 0.


def test_same_title_or_text_never_merges_distinct_resource_ids():
    chunks = {key: {'resource_id': key, 'version_id': key, 'text': 'same', 'title': 'same'} for key in ('a', 'b')}
    assert len(grouped_hits({'a': .8, 'b': .8}, chunks)) == 2


def test_partial_topic_match_is_not_an_answer_but_ndcg_preserves_grade():
    result = evaluate_ranking(['topic', 'answer'], {'topic': 1, 'answer': 3})
    assert result['precision1'] == 0.
    assert result['mrr10'] == .5
    assert 0 < result['ndcg10'] < 1


def test_no_answer_metrics_are_separate_from_recall():
    row = {'metrics': evaluate_ranking([], {}), 'latency_ms': 10.}
    assert row['metrics']['recall5'] is None
    result = summarize([row])
    assert result['no_answer_false_positive_rate'] == 0.
    assert result['recall5'] is None


def test_validation_only_threshold_cannot_inspect_test_queries():
    with pytest.raises(ValueError, match='Validation-only'):
        calibrate_threshold([{'split': 'test', 'query_id': 'heldout'}])
    rows = [
        {'split': 'validation', 'query_id': 'a', 'qrels': {'answer': 3},
         'hits': [{'id': 'answer', 'score': .8}, {'id': 'noise', 'score': .4}]},
        {'split': 'validation', 'query_id': 'b', 'qrels': {}, 'hits': [{'id': 'noise', 'score': .3}]},
    ]
    result = calibrate_threshold(rows)
    assert result['selected']['threshold'] == .4
    assert result['selected']['recall'] == 1.
    assert result['validation_query_ids'] == ['a', 'b']


def test_candidate_diversity_and_stable_ties():
    assert diverse_candidates({'a': .8, 'b': .8, 'c': .7}, {'a': 'v1', 'b': 'v1', 'c': 'v2'}, 2) == ['a', 'c']
    with pytest.raises(ValueError):
        diverse_candidates({}, {}, 0)


def test_qrels_require_content_evidence_not_title_or_model_rank():
    corpus = {'checksum': 'frozen', 'resources': [{'resource_id': 'r', 'version_id': 'v',
        'resource_title': 'fractions', 'chunks': [{'id': 'c', 'text': 'Quy đồng mẫu số'}]}]}
    specification = {'queries': [{'query_id': 'q', 'query': 'common denominator', 'split': 'test',
        'language': 'en', 'category': 'cross_lingual', 'annotations': [
            {'resource_ids': ['r'], 'grade': 3, 'anchors': ['quy đồng']}] }]}
    result = compile_qrels(corpus, specification)
    assert result['queries'][0]['chunk_qrels'] == {'c': 3}
    assert result['queries'][0]['evidence'][0]['resource_version_id'] == 'v'
    specification['queries'][0]['annotations'][0]['anchors'] = ['fractions']
    with pytest.raises(ValueError, match='no supporting chunk'):
        compile_qrels(corpus, specification)


@pytest.mark.parametrize('bad', [[math.nan], [math.inf], [True]])
def test_invalid_scores_are_rejected(bad):
    with pytest.raises(ValueError):
        validate_scores(bad)
