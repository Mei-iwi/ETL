"""Private, blinded evidence export and human-review consistency validation.

Never approves labels automatically. No DB, model, search ranking or runtime change.
"""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from data_sync_etl.application.vector_quality import compile_qrels
from data_sync_etl.domain.evaluation import checksum


def private(path):
    if not path.resolve().is_relative_to(Path(".test-tmp").resolve()):
        raise ValueError("Review evidence must stay under private .test-tmp")


def export(corpus_path, spec_path, output, query_id=None):
    private(output)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if checksum({k: v for k, v in corpus.items() if k != "checksum"}) != corpus["checksum"]:
        raise ValueError("Corpus checksum mismatch")
    qrels = compile_qrels(corpus, spec)
    queries = [q for q in qrels["queries"] if query_id is None or q["query_id"] == query_id]
    count = len(queries) * sum(len(r["chunks"]) for r in corpus["resources"])
    if not queries or count > 30000:
        raise ValueError("Bounded review export required")
    reasons = {q["query_id"]: q.get("reason") for q in spec["queries"]}
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError("Use a fresh file; never overwrite human review")
    with output.open("w", encoding="utf-8") as stream:
        for q in queries:
            for resource in sorted(corpus["resources"], key=lambda r: r["resource_id"]):
                for chunk in resource["chunks"]:
                    row = {
                        "corpus_checksum": corpus["checksum"],
                        "specification_checksum": qrels["specification_checksum"],
                        "query_id": q["query_id"],
                        "query": q["query"],
                        "language": q["language"],
                        "resource_id": resource["resource_id"],
                        "resource_version_id": resource["version_id"],
                        "chunk_id": chunk["id"],
                        "evidence": chunk["text"],
                        "evidence_hash": checksum(chunk["text"]),
                        "draft_grade": q["chunk_qrels"].get(chunk["id"]),
                        "draft_reason": reasons[q["query_id"]]
                        or "Anchor in body; draft annotation requires independent review.",
                        "reviewer_id": "",
                        "grade": None,
                        "reason": "",
                        "status": "DRAFT",
                    }
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {
        "status": "DRAFT",
        "rows": count,
        "queries": len(queries),
        "blinded_to_model_scores": True,
    }


def validate(corpus_path, spec_path, review_paths):
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if checksum({k: v for k, v in corpus.items() if k != "checksum"}) != corpus["checksum"]:
        raise ValueError("Corpus checksum mismatch")
    spec_hash = checksum(spec)
    query_ids = {q["query_id"] for q in spec["queries"]}
    evidence = {
        (r["resource_id"], r["version_id"], c["id"]): checksum(c["text"])
        for r in corpus["resources"]
        for c in r["chunks"]
    }
    judgments = defaultdict(dict)
    statuses = defaultdict(int)
    for path in review_paths:
        private(path)
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                key = (row["resource_id"], row["resource_version_id"], row["chunk_id"])
                if (
                    row["corpus_checksum"] != corpus["checksum"]
                    or row["specification_checksum"] != spec_hash
                    or row["query_id"] not in query_ids
                    or evidence.get(key) != row["evidence_hash"]
                    or checksum(row["evidence"]) != row["evidence_hash"]
                ):
                    raise ValueError("Review identity/evidence mismatch")
                status = row["status"]
                if status not in ("DRAFT", "REVIEWED", "APPROVED"):
                    raise ValueError("Invalid review status")
                statuses[status] += 1
                if status == "DRAFT":
                    continue
                reviewer = row["reviewer_id"].strip()
                if (
                    not reviewer
                    or not row["reason"].strip()
                    or type(row["grade"]) is not int
                    or not 0 <= row["grade"] <= 3
                ):
                    raise ValueError(
                        "Reviewed judgments require reviewer, reason and integer grade0..3"
                    )
                identity = (row["query_id"], *key)
                if reviewer in judgments[identity]:
                    raise ValueError("Duplicate judgment by same reviewer")
                judgments[identity][reviewer] = row["grade"]
    conflicts = [
        {
            "query_id": key[0],
            "resource_id": key[1],
            "resource_version_id": key[2],
            "chunk_id": key[3],
            "reviewer_count": len(values),
            "grades": sorted(set(values.values())),
        }
        for key, values in judgments.items()
        if len(set(values.values())) > 1
    ]
    return {
        "status": "CONFLICTS_REQUIRE_HUMAN_RESOLUTION" if conflicts else "REVIEW_AUDIT_ONLY",
        "status_counts": dict(statuses),
        "judged_evidence_items": len(judgments),
        "disagreements": conflicts,
        "automatic_label_promotion": False,
        "runtime_changed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("export", "validate"))
    parser.add_argument(
        "--corpus", type=Path, default=Path(".test-tmp/semantic-quality/verified/corpus.json")
    )
    parser.add_argument(
        "--spec", type=Path, default=Path("fixtures/search/semantic-quality/optimization/queries-v1.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".test-tmp/semantic-quality/optimization/review.jsonl")
    )
    parser.add_argument("--query-id")
    parser.add_argument("--reviews", type=Path, nargs="*", default=[])
    args = parser.parse_args()
    if args.operation == "validate" and not args.reviews:
        parser.error("--reviews required for validate")
    result = (
        export(args.corpus, args.spec, args.output, args.query_id)
        if args.operation == "export"
        else validate(args.corpus, args.spec, args.reviews)
    )
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
