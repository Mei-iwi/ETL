from copy import deepcopy

import pytest

from data_sync_etl.application.semantic_selection import (
    assess,
    bounded_aggregate,
    calibrate,
    features,
    judgment_distribution,
    select,
)
from data_sync_etl.application.vector_quality import evaluate_ranking


def row(qid, scores, qrels, split="validation"):
    hits = [{"id": key, "score": value} for key, value in scores.items()]
    return {
        "query_id": qid,
        "split": split,
        "hits": hits,
        "qrels": qrels,
        "metrics": evaluate_ranking(list(scores), qrels),
    }


def test_empty_scope_never_becomes_confident_and_single_candidate_has_no_margin():
    policy = {"absolute": 0.0, "margin": 0.01, "feature": "gap5"}
    assert select([], policy) == []
    assert features([{"id": "a", "score": 0.9}]) == {"top": 0.9, "gap2": 0.0, "gap5": 0.0}
    assert select([{"id": "a", "score": 0.9}], policy) == []


def test_absolute_threshold_zero_keeps_positive_noise_and_positive_threshold_abstains():
    hits = [{"id": "noise", "score": 0.2}]
    assert select(hits, {"absolute": 0.0, "margin": 0.0, "feature": "gap2"}) == hits
    assert select(hits, {"absolute": 0.2, "margin": 0.0, "feature": "gap2"}) == []


def test_duplicate_resources_must_be_grouped_before_confidence():
    with pytest.raises(ValueError, match="grouping"):
        features([{"id": "a", "score": 0.8}, {"id": "a", "score": 0.7}])


def test_duplicate_content_separate_resource_makes_top2_margin_unreliable():
    hits = [
        {"id": "a", "score": 0.8},
        {"id": "b", "score": 0.8},
        {"id": "c", "score": 0.3},
        {"id": "d", "score": 0.2},
        {"id": "e", "score": 0.1},
    ]
    assert features(hits)["gap2"] == 0
    assert features(hits)["gap5"] == pytest.approx(0.7)
    assert select(hits, {"absolute": 0.0, "margin": 0.1, "feature": "gap2"}) == []
    assert len(select(hits, {"absolute": 0.0, "margin": 0.1, "feature": "gap5"})) == 5


def test_partial_relevance_is_not_an_answer_and_false_abstention_is_separate():
    rows = [row("partial", {"a": 0.6}, {"a": 1}), row("positive", {"b": 0.5}, {"b": 3})]
    result, _ = assess(rows, {"absolute": 0.55, "margin": 0.0, "feature": "gap5"})
    assert result["no_answer_false_positive_rate"] == 1.0
    assert result["false_abstention_rate"] == 1.0
    assert result["recall5"] == 0.0


def test_selective_calibration_rejects_any_holdout_row():
    with pytest.raises(ValueError, match="Validation"):
        calibrate([row("h", {"a": 0.5}, {"a": 3}, split="test")])


def test_calibration_preserves_recall_instead_of_claiming_noise_can_always_be_rejected():
    # Semantic overlap cannot be fixed safely by raising a threshold.
    rows = [row("p", {"a": 0.4}, {"a": 3}), row("n", {"b": 0.8}, {"b": 1})]
    result = calibrate(rows)
    chosen = result["methods"]["absolute"]["selected"]
    assert chosen["metrics"]["recall5"] == 1.0
    assert chosen["metrics"]["false_abstention_rate"] == 0.0
    assert chosen["metrics"]["no_answer_false_positive_rate"] == 1.0
    assert result["validation_query_ids"] == ["p", "n"]


def test_calibration_can_reject_easy_no_answer_without_inventing_probability():
    result = calibrate([row("p", {"a": 0.8}, {"a": 3}), row("n", {"b": 0.2}, {})])
    chosen = result["methods"]["absolute"]["selected"]
    assert chosen["policy"]["absolute"] == 0.2
    assert chosen["metrics"]["no_answer_false_positive_rate"] == 0.0
    assert "probability" not in chosen


@pytest.mark.parametrize("scores", [{"a": float("nan")}, {"a": float("inf")}, {"a": True}])
def test_nonfinite_features_rejected(scores):
    with pytest.raises(ValueError):
        features([{"id": key, "score": value} for key, value in scores.items()])


def test_bounded_aggregation_has_no_unbounded_chunk_count_bonus_and_stable_locator():
    chunks = {str(i): {"resource_id": "a", "version_id": "va"} for i in range(100)}
    chunks["z"] = {"resource_id": "b", "version_id": "vb"}
    scores = {str(i): 0.7 for i in range(100)}
    scores["z"] = 0.8
    before = deepcopy(scores), deepcopy(chunks)
    for policy in ("best", "mean", "evidence"):
        hits = bounded_aggregate(scores, chunks, policy=policy, top_n=3, evidence_scores=scores)
        assert [h["id"] for h in hits] == ["b", "a"]
        assert hits[1]["chunk_id"] == "0"
        assert hits[1]["score"] == pytest.approx(0.7)
    assert before == (scores, chunks)


def test_evidence_selection_returns_locator_that_actually_decides_score():
    chunks = {
        "a1": {"resource_id": "a", "version_id": "va"},
        "a2": {"resource_id": "a", "version_id": "va"},
    }
    hits = bounded_aggregate(
        {"a1": 0.9, "a2": 0.8},
        chunks,
        policy="evidence",
        evidence_scores={"a1": 0.1, "a2": 0.8},
        alpha=0.5,
    )
    assert hits[0]["chunk_id"] == "a2"
    assert hits[0]["score"] == 0.8
    with pytest.raises(ValueError, match="Evidence"):
        bounded_aggregate({"a1": 0.9}, chunks, policy="evidence")


def test_explicit_negative_unjudged_and_query_no_answer_are_distinct():
    rows = [
        row("q", {"a": 0.7, "b": 0.6, "c": 0.5, "d": 0.4}, {"a": 3, "b": 1, "c": 0}),
        row("no-answer", {"a": 0.6}, {"a": 1}),
    ]
    result = judgment_distribution(rows)
    assert result["answer"]["count"] == 1
    assert result["partial"]["count"] == 2
    assert result["judged_negative"]["count"] == 1
    assert result["unjudged"]["count"] == 1
    assert result["no_answer_query_top"]["count"] == 1


def test_aggregation_never_mixes_versions_of_same_resource():
    chunks = {
        "c1": {"resource_id": "r", "version_id": "old"},
        "c2": {"resource_id": "r", "version_id": "new"},
    }
    with pytest.raises(ValueError, match="verified version"):
        bounded_aggregate({"c1": 0.9, "c2": 0.8}, chunks)
