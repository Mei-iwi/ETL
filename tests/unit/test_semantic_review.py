import json

import pytest

from data_sync_etl.domain.evaluation import checksum
from scripts.review_semantic_qrels import export, validate


@pytest.fixture
def review_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    private = tmp_path / ".test-tmp"
    private.mkdir()
    corpus = {
        "resources": [
            {
                "resource_id": "r",
                "version_id": "v",
                "chunks": [{"id": "c", "text": "Body evidence"}],
            }
        ]
    }
    corpus["checksum"] = checksum(corpus)
    spec = {
        "label_status": "DRAFT",
        "queries": [
            {
                "query_id": "q",
                "query": "Question",
                "language": "en",
                "category": "explanation",
                "split": "validation",
                "annotations": [{"resource_ids": ["r"], "anchors": ["Body"], "grade": 3}],
            }
        ],
    }
    c, s = private / "corpus.json", private / "spec.json"
    c.write_text(json.dumps(corpus), encoding="utf-8")
    s.write_text(json.dumps(spec), encoding="utf-8")
    return c, s, private / "review.jsonl"


def test_export_is_draft_blinded_and_never_overwrites_human_work(review_files):
    c, s, out = review_files
    assert export(c, s, out)["status"] == "DRAFT"
    row = json.loads(out.read_text(encoding="utf-8"))
    assert row["evidence"] == "Body evidence" and row["draft_grade"] == 3
    assert row["grade"] is None and not row["reviewer_id"]
    assert "score" not in row
    with pytest.raises(ValueError, match="overwrite"):
        export(c, s, out)
    assert validate(c, s, [out])["judged_evidence_items"] == 0


def test_conflicting_approved_reviews_are_not_automatically_promoted(review_files):
    c, s, out = review_files
    export(c, s, out)
    first = json.loads(out.read_text(encoding="utf-8"))
    first.update(status="APPROVED", reviewer_id="reviewer-A", grade=3, reason="Direct evidence")
    second = {**first, "reviewer_id": "reviewer-B", "grade": 1, "reason": "Only partial evidence"}
    out.write_text(json.dumps(first) + "\n" + json.dumps(second) + "\n", encoding="utf-8")
    result = validate(c, s, [out])
    assert result["status"] == "CONFLICTS_REQUIRE_HUMAN_RESOLUTION"
    assert result["disagreements"][0]["grades"] == [1, 3]
    assert result["automatic_label_promotion"] is False


def test_approval_requires_identity_reason_grade_and_unmodified_evidence(review_files):
    c, s, out = review_files
    export(c, s, out)
    row = json.loads(out.read_text(encoding="utf-8"))
    row["status"] = "APPROVED"
    out.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer"):
        validate(c, s, [out])
    row.update(reviewer_id="reviewer", reason="Read body", grade=3, evidence="Changed body")
    out.write_text(json.dumps(row), encoding="utf-8")
    with pytest.raises(ValueError, match="mismatch"):
        validate(c, s, [out])


def test_full_review_evidence_cannot_be_exported_into_source_tree(review_files, tmp_path):
    c, s, _ = review_files
    with pytest.raises(ValueError, match="private"):
        export(c, s, tmp_path / "public.jsonl")
