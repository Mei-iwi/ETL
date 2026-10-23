"""Versioned offline optimization: prepare vectors, lock validation choices, then holdout.

No application Container, database connection, runtime flags, credentials or reindex.
Raw corpus/vectors stay under private .test-tmp; reports contain only IDs/metrics.
"""

import argparse
import json
import os
import time
from pathlib import Path

from data_sync_etl.application.semantic_selection import (
    assess,
    bounded_aggregate,
    calibrate,
    judgment_distribution,
)
from data_sync_etl.application.vector_quality import compile_qrels, evaluate_ranking, summarize
from data_sync_etl.domain.evaluation import checksum
from data_sync_etl.domain.search import normalize_query
from data_sync_etl.domain.semantic import embedding_input

STRATEGIES = (
    "raw_content",
    "title_content",
    "title_heading_content",
    "subject_grade_content",
    "verified_full_context",
)
OLD_NAMES = {
    "raw_content": "raw",
    "title_content": "title_only",
    "verified_full_context": "contextual",
}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def inputs(corpus, strategy):
    texts, locators = [], {}
    for resource in corpus["resources"]:
        meta = resource["metadata"]
        for chunk in resource["chunks"]:
            locators[chunk["id"]] = {
                **chunk,
                "resource_id": resource["resource_id"],
                "version_id": resource["version_id"],
            }
            texts.append(
                embedding_input(
                    chunk,
                    title=meta.get("title")
                    if strategy not in ("raw_content", "subject_grade_content")
                    else None,
                    subjects=meta.get("subjects", ())
                    if strategy in ("subject_grade_content", "verified_full_context")
                    else (),
                    grades=meta.get("grades", ())
                    if strategy in ("subject_grade_content", "verified_full_context")
                    else (),
                    headings=chunk["headings"]
                    if strategy in ("title_heading_content", "verified_full_context")
                    else (),
                    strategy="raw" if strategy == "raw_content" else "contextual",
                )
            )
    return texts, locators


def check_vectors(path, count):
    import numpy as np

    with np.load(path, allow_pickle=False) as artifact:
        vectors = artifact["vectors"]
    if (
        vectors.shape != (count, 1024)
        or not np.isfinite(vectors).all()
        or not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)
    ):
        raise ValueError("Invalid frozen vector artifact")
    return vectors


def context(args):
    corpus, spec = load(args.corpus), load(args.spec)
    if (
        not args.output.resolve().is_relative_to(Path(".test-tmp").resolve())
        or len(corpus["resources"]) > 100
        or len(spec["queries"]) > 100
        or checksum({k: v for k, v in corpus.items() if k != "checksum"}) != corpus["checksum"]
    ):
        raise ValueError("Bounded private output and verified corpus required")
    qrels = compile_qrels(corpus, spec)
    freeze = load(args.spec.with_name("freeze.json"))
    if (
        freeze["corpus_checksum"] != corpus["checksum"]
        or freeze["specification_checksum"] != checksum(spec)
        or freeze["qrels_checksum"] != checksum(qrels)
    ):
        raise ValueError("Frozen holdout identity mismatch")
    return corpus, spec, qrels


def prepare(args):
    os.environ["HF_HUB_OFFLINE"] = os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import numpy as np
    import torch

    from data_sync_etl.adapters.bge_m3 import BgeM3Embedding

    torch.set_num_threads(2)
    corpus, _, qrels = context(args)
    model = BgeM3Embedding(args.model_path, args.revision, batch_size=2, max_tokens=1024)
    start = time.perf_counter()
    model.prepare()
    result = {
        "corpus_checksum": corpus["checksum"],
        "specification_checksum": qrels["specification_checksum"],
        "model": model.model_identity(),
        "model_load_seconds": time.perf_counter() - start,
        "artifacts": {},
    }

    def encode(texts, name, old_name=None):
        identity = checksum([corpus["checksum"], model.model_identity(), texts, old_name or name])
        prior = args.baseline / (f"{old_name}-{identity[:16]}.npz") if old_name else None
        dest = args.output / f"{name}-{identity[:16]}.npz"
        artifact = prior if prior and prior.exists() else dest
        if artifact.exists():
            check_vectors(artifact, len(texts))
            return {
                "path": str(artifact),
                "cache_hit": True,
                "encode_seconds": None,
                "identity": identity,
            }
        start = time.perf_counter()
        rows = []
        for offset in range(0, len(texts), 2):
            rows.extend(row["vector"] for row in model.embed_documents(texts[offset : offset + 2]))
            if offset % 80 == 0:
                print(
                    json.dumps(
                        {
                            "phase": name,
                            "completed": offset + len(texts[offset : offset + 2]),
                            "total": len(texts),
                        }
                    ),
                    flush=True,
                )
        args.output.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(dest, vectors=np.asarray(rows, dtype=np.float32))
        return {
            "path": str(dest),
            "cache_hit": False,
            "encode_seconds": time.perf_counter() - start,
            "identity": identity,
        }

    for strategy in STRATEGIES:
        texts, _ = inputs(corpus, strategy)
        result["artifacts"][strategy] = encode(texts, strategy, OLD_NAMES.get(strategy))
        save(args.output / "prepared.json", result)
    texts = [normalize_query(row["query"]) for row in qrels["queries"]]
    result["artifacts"]["new_queries"] = encode(texts, "optimization_queries")
    save(args.output / "prepared.json", result)
    print(
        json.dumps(
            {"status": "PASS", "operation": "prepare_offline_vectors", "runtime_changed": False}
        ),
        flush=True,
    )


def evaluate(args):
    import numpy as np

    corpus, _, new_qrels = context(args)
    prepared = load(args.output / "prepared.json")
    old_spec = load(args.spec.parent.parent / "query-specification.json")
    old_qrels = compile_qrels(corpus, old_spec)
    old_queries = [normalize_query(row["query"]) for row in old_qrels["queries"]]
    identity = checksum([corpus["checksum"], prepared["model"], old_queries, "queries"])
    old_vectors = check_vectors(args.baseline / f"queries-{identity[:16]}.npz", len(old_queries))
    new_vectors = check_vectors(
        Path(prepared["artifacts"]["new_queries"]["path"]), len(new_qrels["queries"])
    )
    _, locators = inputs(corpus, "raw_content")
    keys = list(locators)
    if (
        prepared["corpus_checksum"] != corpus["checksum"]
        or prepared["specification_checksum"] != new_qrels["specification_checksum"]
    ):
        raise ValueError("Prepared artifacts do not match frozen evaluation")
    vectors = {
        strategy: check_vectors(Path(prepared["artifacts"][strategy]["path"]), len(keys))
        for strategy in STRATEGIES
    }
    validation = [
        (q, old_vectors[i])
        for i, q in enumerate(old_qrels["queries"])
        if q["split"] == "validation"
    ]
    validation += [
        (q, new_vectors[i])
        for i, q in enumerate(new_qrels["queries"])
        if q["split"] == "validation"
    ]
    holdout = [
        (q, new_vectors[i]) for i, q in enumerate(new_qrels["queries"]) if q["split"] == "test"
    ]

    def rows_for(pairs, strategy, policy="best", top_n=3, alpha=0.5):
        rows = []
        for query, vector in pairs:
            dense = np.clip(vectors[strategy] @ vector, -1.0, 1.0).astype(float)
            raw = np.clip(vectors["raw_content"] @ vector, -1.0, 1.0).astype(float)
            scores = dict(zip(keys, dense, strict=True))
            evidence = dict(zip(keys, raw, strict=True))
            hits = bounded_aggregate(
                scores, locators, policy=policy, top_n=top_n, evidence_scores=evidence, alpha=alpha
            )
            chunk_order = sorted(keys, key=lambda key: (-scores[key], key))
            row = {
                **query,
                "strategy": strategy,
                "policy": policy,
                "top_n": top_n,
                "alpha": alpha,
                "hits": hits,
                "metrics": evaluate_ranking([hit["id"] for hit in hits], query["resource_qrels"]),
                "chunk_metrics": evaluate_ranking(chunk_order, query["chunk_qrels"]),
                "snippet_metrics": evaluate_ranking(
                    [hit["chunk_id"] for hit in hits], query["chunk_qrels"]
                ),
                "qrels": query["resource_qrels"],
            }
            rows.append(row)
        return rows

    summaries = {}
    validation_rows = {}
    for strategy in STRATEGIES:
        rows = rows_for(validation, strategy)
        validation_rows[strategy] = rows
        summaries[strategy] = {
            "resource": summarize(rows),
            "snippet": summarize([{**row, "metrics": row["snippet_metrics"]} for row in rows]),
            "chunk": summarize([{**row, "metrics": row["chunk_metrics"]} for row in rows]),
        }
    # Predeclared selection: resource P@1, snippet P@1, MRR; ties prefer current full context.
    selected = max(
        STRATEGIES,
        key=lambda s: (
            summaries[s]["resource"]["precision1"],
            summaries[s]["snippet"]["precision1"],
            summaries[s]["resource"]["mrr10"],
            s == "verified_full_context",
        ),
    )
    aggregation = {}
    choices = []
    for policy, n, alpha in [
        ("best", 1, 0.5),
        ("mean", 2, 0.5),
        ("mean", 3, 0.5),
        *[("evidence", 3, a) for a in (0.0, 0.25, 0.5, 0.75, 1.0)],
    ]:
        rows = rows_for(validation, selected, policy, n, alpha)
        resource = summarize(rows)
        snippet = summarize([{**row, "metrics": row["snippet_metrics"]} for row in rows])
        name = f"{policy}-n{n}-a{alpha}"
        aggregation[name] = {"resource": resource, "snippet": snippet}
        if resource["precision1"] >= summaries[selected]["resource"]["precision1"]:
            choices.append(
                (
                    snippet["precision1"],
                    resource["precision1"],
                    resource["mrr10"],
                    policy == "best",
                    name,
                    policy,
                    n,
                    alpha,
                )
            )
    *_, aggregation_name, policy, n, alpha = max(choices)
    chosen_rows = rows_for(validation, selected, policy, n, alpha)
    calibration = calibrate(chosen_rows)
    # Lock before looking at ANY new holdout ranking or score. Never tune below this line.
    lock = {
        "corpus_checksum": corpus["checksum"],
        "specification_checksum": new_qrels["specification_checksum"],
        "strategy": selected,
        "aggregation": {"name": aggregation_name, "policy": policy, "top_n": n, "alpha": alpha},
        "selection": {k: v["selected"] for k, v in calibration["methods"].items()},
        "validation_query_ids": [q["query_id"] for q, _ in validation],
        "holdout_evaluation_count": 1,
    }
    lock_path = args.output / "selection-lock.json"
    if lock_path.exists() and load(lock_path) != lock:
        raise ValueError("Existing validation lock differs; holdout must not be retuned")
    save(lock_path, lock)
    result = {
        "status": "PARTIAL",
        "label_status": new_qrels["label_status"],
        "scope": "OFFLINE_NOT_LIVE_API",
        "corpus_checksum": corpus["checksum"],
        "specification_checksum": new_qrels["specification_checksum"],
        "validation": summaries,
        "validation_aggregation": aggregation,
        "lock": lock,
        "holdout": {},
        "selection": {},
        "rows": [],
    }
    for strategy in STRATEGIES:
        rows = rows_for(holdout, strategy)
        result["holdout"][strategy] = {
            "resource": summarize(rows),
            "snippet": summarize([{**row, "metrics": row["snippet_metrics"]} for row in rows]),
            "chunk": summarize([{**row, "metrics": row["chunk_metrics"]} for row in rows]),
        }
        result["rows"].extend(rows)
    held = rows_for(holdout, selected, policy, n, alpha)
    result['score_judgments'] = {
        'validation': judgment_distribution(chosen_rows), 'holdout': judgment_distribution(held)}
    for name, method in calibration["methods"].items():
        chosen = method["selected"]
        if chosen is None:
            result["selection"][name] = {"status": "NO_FEASIBLE_VALIDATION_POLICY"}
            continue
        m, rows = assess(held, chosen["policy"])
        result["selection"][name] = {
            "policy": chosen["policy"],
            "validation": chosen["metrics"],
            "holdout": m,
            "by_language": {
                lang: assess([r for r in held if r["language"] == lang], chosen["policy"])[0]
                for lang in sorted({r["language"] for r in held})
            },
            "by_category": {
                cat: assess([r for r in held if r["category"] == cat], chosen["policy"])[0]
                for cat in sorted({r["category"] for r in held})
            },
            "decisions": [
                {
                    "query_id": r["query_id"],
                    "accepted": bool(r["hits"]),
                    "top_grade": r["qrels"].get(r["hits"][0]["id"]) if r["hits"] else None,
                }
                for r in rows
            ],
        }
    result["selected_aggregation_holdout"] = {
        "resource": summarize(held),
        "snippet": summarize([{**row, "metrics": row["snippet_metrics"]} for row in held]),
    }
    result["duplicate_sensitivity"] = {}
    # Descriptive only, not retuning: collapse only exact frozen content fingerprints for analysis.
    families = {
        r["resource_id"]: checksum([c["text"] for c in r["chunks"]]) for r in corpus["resources"]
    }
    for strategy in STRATEGIES:
        rows = [r for r in result["rows"] if r["strategy"] == strategy]
        collapsed = []
        for row in rows:
            ranked = list(dict.fromkeys(families[h["id"]] for h in row["hits"]))
            grades = {}
            for resource, grade in row["qrels"].items():
                grades[families[resource]] = max(grades.get(families[resource], 0), grade)
            collapsed.append({**row, "metrics": evaluate_ranking(ranked, grades)})
        result["duplicate_sensitivity"][strategy] = summarize(collapsed)
    # Report locators/grades without leaking full extracted content or vectors.
    for row in result["rows"]:
        row["hits"] = row["hits"][:10]
        row.pop("evidence", None)
    save(args.output / "results.json", result)
    save(args.output / "calibration-curves.json", calibration)
    print(
        json.dumps(
            {
                "status": "PARTIAL",
                "operation": "locked_holdout_evaluation",
                "strategy": selected,
                "aggregation": aggregation_name,
                "holdout_queries": len(holdout),
                "runtime_changed": False,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "evaluate"))
    parser.add_argument(
        "--corpus", type=Path, default=Path(".test-tmp/semantic-quality/verified/corpus.json")
    )
    parser.add_argument(
        "--spec", type=Path, default=Path("research/semantic-quality/optimization/queries-v1.json")
    )
    parser.add_argument(
        "--baseline", type=Path, default=Path(".test-tmp/semantic-quality/experiment")
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".test-tmp/semantic-quality/optimization")
    )
    parser.add_argument("--model-path", type=Path, default=Path("Models/bge-m3"))
    parser.add_argument("--revision", default="5617a9f61b028005a4858fdac845db406aefb181")
    args = parser.parse_args()
    (prepare if args.phase == "prepare" else evaluate)(args)


if __name__ == "__main__":
    main()
