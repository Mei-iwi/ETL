"""Real offline BGE-M3 profiling, bounded samples; no DB or authenticated API claim.

Use a dedicated process/container. Writes metrics/IDs only; model path must be local.
"""

import argparse
import json
import os
import threading
import time
from pathlib import Path

from data_sync_etl.domain.evaluation import checksum, percentile
from data_sync_etl.domain.search import normalize_query
from scripts.optimize_semantic_quality import inputs, load, save


def run(args):
    os.environ["HF_HUB_OFFLINE"] = os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import numpy as np
    import psutil
    import torch

    from data_sync_etl.adapters.bge_m3 import BgeM3Embedding

    corpus = load(args.corpus)
    if (
        len(corpus["resources"]) > 100
        or checksum({k: v for k, v in corpus.items() if k != "checksum"}) != corpus["checksum"]
    ):
        raise ValueError("Verified bounded corpus required")
    spec = load(args.spec)
    process = psutil.Process()
    stopped = threading.Event()
    peak = [0]

    def sample():
        while not stopped.wait(0.02):
            peak[0] = max(peak[0], process.memory_info().rss)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    torch.set_num_threads(2)
    model = BgeM3Embedding(args.model_path, args.revision, batch_size=2, max_tokens=1024)
    start = time.perf_counter()
    cpu = time.process_time()
    model.prepare()
    report = {
        "scope": "OFFLINE_REAL_MODEL_NOT_LIVE_HTTP",
        "model": model.model_identity(),
        "corpus_checksum": corpus["checksum"],
        "model_load_seconds": time.perf_counter() - start,
        "model_load_cpu_seconds": time.process_time() - cpu,
        "host_cpu_count": os.cpu_count(),
        "queries": {},
        "ingestion": [],
        "colbert": [],
        "live_stages": {
            k: None
            for k in (
                "authentication",
                "mongo_retrieval",
                "authorization",
                "HTTP_total",
                "HTTP_serialization",
            )
        },
        "query_cache": "disabled",
        "model_load_count": 1,
    }
    identity = id(model._model)
    import unicodedata

    nfc = normalize_query(next(q["query"] for q in spec["queries"] if q["language"] == "vi"))
    nfd = normalize_query(unicodedata.normalize("NFD", nfc))
    a, b = model.embed_queries([nfc, nfd])
    report["unicode_parity"] = {
        "normalized_identical": nfc == nfd,
        "cosine": float(np.dot(a["vector"], b["vector"])),
    }
    try:
        model.embed_queries(["token " * 1100])
    except Exception as exc:
        report["overlength_query"] = {
            "code": getattr(exc, "code", "UNEXPECTED_ERROR"),
            "truncated": False,
        }
    else:
        raise ValueError("Overlength query was not rejected")
    texts, locators = inputs(corpus, "verified_full_context")
    keys = list(locators)
    lengths = [len(model._model.tokenizer.encode(text, truncation=False)) for text in texts]
    report["token_lengths"] = {
        "chunks": len(texts),
        "min": min(lengths),
        "p50": percentile(lengths, 0.5),
        "p95": percentile(lengths, 0.95),
        "max": max(lengths),
        "over_budget": sum(n > 1024 for n in lengths),
    }
    # Only validation queries; the holdout is not used to choose CPU/batch settings.
    queries = [q for q in spec["queries"] if q["split"] == "validation"][:12]
    report["first_timed_query_after_unicode_warmup_seconds"] = None
    for threads in () if args.colbert_only else (1, 2, 4):
        torch.set_num_threads(threads)
        observations = []
        cpu = time.process_time()
        wall = time.perf_counter()
        for repeat in range(3):
            for query in queries:
                start = time.perf_counter()
                normalized = normalize_query(query["query"])
                preprocess = (time.perf_counter() - start) * 1000
                start = time.perf_counter()
                model.embed_queries([normalized])
                elapsed = (time.perf_counter() - start) * 1000
                if report["first_timed_query_after_unicode_warmup_seconds"] is None:
                    report["first_timed_query_after_unicode_warmup_seconds"] = elapsed / 1000
                observations.append(
                    {
                        "query_id": query["query_id"],
                        "language": query["language"],
                        "repeat": repeat,
                        "encode_ms": elapsed,
                        "preprocessing_ms": preprocess,
                        "tokens": len(model._model.tokenizer.encode(normalized, truncation=False)),
                    }
                )
        times = [r["encode_ms"] for r in observations]
        warm = [r["encode_ms"] for r in observations if r["repeat"] > 0]
        report["queries"][str(threads)] = {
            "samples": len(times),
            "p50_ms": percentile(times, 0.5),
            "p95_ms": percentile(times, 0.95),
            "p99_ms": None,
            "warm_p50_ms": percentile(warm, 0.5),
            "warm_p95_ms": percentile(warm, 0.95),
            "cpu_seconds": time.process_time() - cpu,
            "wall_seconds": time.perf_counter() - wall,
            "observations": observations,
        }
        save(args.output, report)
        print(
            json.dumps(
                {"phase": "query_profile", "threads": threads, "p95_ms": percentile(times, 0.95)}
            ),
            flush=True,
        )
    torch.set_num_threads(2)
    # Twelve strata across token lengths; same documents and order are reused in every grid cell.
    ordered = sorted(range(len(texts)), key=lambda i: (lengths[i], keys[i]))
    sample_ids = [ordered[round(i * (len(ordered) - 1) / 11)] for i in range(12)]
    original = [texts[i] for i in sample_ids]
    reference = None
    for batch_size in () if args.colbert_only else (1, 2, 4, 8):
        if batch_size == 8 and peak[0] > int(3.2 * 1024**3):
            report["ingestion"].append({"batch_size": 8, "status": "NOT_RUN_MEMORY_GUARD"})
            break
        model.batch_size = batch_size
        for policy in ("length_ascending", "length_descending"):
            order = list(range(len(original)))
            if policy == "length_descending":
                order.sort(key=lambda i: (-lengths[sample_ids[i]], keys[sample_ids[i]]))
            cpu = time.process_time()
            wall = time.perf_counter()
            vectors = {}
            for offset in range(0, len(order), batch_size):
                selected = order[offset : offset + batch_size]
                encoded = model.embed_documents([original[i] for i in selected])
                for index, row in zip(selected, encoded, strict=True):
                    vectors[index] = row["vector"]
            values = np.asarray([vectors[i] for i in range(len(original))])
            if reference is None:
                reference = values
            duration = time.perf_counter() - wall
            report["ingestion"].append(
                {
                    "status": "PASS",
                    "batch_size": batch_size,
                    "policy": policy,
                    "samples": len(original),
                    "wall_seconds": duration,
                    "cpu_seconds": time.process_time() - cpu,
                    "chunks_per_second": len(original) / duration,
                    "min_cosine_to_batch1": float(np.sum(values * reference, axis=1).min()),
                    "max_component_difference": float(np.abs(values - reference).max()),
                    "sample_chunk_ids": [keys[i] for i in sample_ids],
                }
            )
            save(args.output, report)
        print(json.dumps({"phase": "ingestion_profile", "batch_size": batch_size}), flush=True)
    model.batch_size = 2
    # Known failure study, never a new holdout tuning pass: full Word vs OCR vs water cycle.
    old = load(args.spec.parent.parent / "qrels.json")
    q = next(q for q in old["queries"] if q["query_id"] == args.case_query_id)
    prior = load(args.spec.parent.parent / "results.json")
    failed = next(
        r
        for r in prior["rerank"]
        if r["query_id"] == args.case_query_id
        and r["limit"] == 10
        and r["policy"] == "version_diverse"
    )["hits"][0]
    answer = next(key for key, grade in q["chunk_qrels"].items() if grade >= 2)
    partial = next(key for key, grade in q["chunk_qrels"].items() if grade == 1)
    candidate = [failed["chunk_id"], answer, partial]
    report["original_failure_top1"] = failed
    token_query = model._encode([normalize_query(q["query"])], colbert=True)[0]["colbert"]
    for strategy in ("raw_content", "verified_full_context"):
        passages, _ = inputs(corpus, strategy)
        for key in candidate:
            start = time.perf_counter()
            encoded = model._encode([passages[keys.index(key)]], colbert=True)[0]
            interactions = token_query @ encoded["colbert"].T
            maxima = interactions.max(axis=1)
            report["colbert"].append(
                {
                    "chunk_id": key,
                    "resource_id": locators[key]["resource_id"],
                    "strategy": strategy,
                    "passage_tokens": encoded["token_count"],
                    "query_token_vectors": len(token_query),
                    "passage_token_vectors": len(encoded["colbert"]),
                    "score": float(maxima.mean()),
                    "maxsim_per_query_token": maxima.astype(float).tolist(),
                    "passage_token_indices_for_maxima": interactions.argmax(axis=1).tolist(),
                    "grade": q["chunk_qrels"].get(key),
                    "truncated": False,
                    "seconds": time.perf_counter() - start,
                }
            )
    report["same_resident_model_all_calls"] = id(model._model) == identity
    report["peak_rss_sampled_bytes"] = peak[0]
    report["rss_sampling_interval_ms"] = 20
    stopped.set()
    monitor.join(timeout=1)
    start = time.perf_counter()
    json.dumps(report)
    report["offline_serialization_ms"] = (time.perf_counter() - start) * 1000
    save(args.output, report)
    print(
        json.dumps({"status": "PASS", "scope": "offline_profile", "peak_rss_bytes": peak[0]}),
        flush=True,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--corpus", type=Path, default=Path(".test-tmp/semantic-quality/verified/corpus.json")
    )
    p.add_argument(
        "--spec", type=Path, default=Path("fixtures/search/semantic-quality/optimization/queries-v1.json")
    )
    p.add_argument("--model-path", type=Path, default=Path("Models/bge-m3"))
    p.add_argument("--revision", default="5617a9f61b028005a4858fdac845db406aefb181")
    p.add_argument(
        "--output", type=Path, default=Path(".test-tmp/semantic-quality/optimization/profile.json")
    )
    p.add_argument(
        "--case-query-id",
        default="B2",
        help="Previously inspected failure case; never holdout tuning",
    )
    p.add_argument(
        "--colbert-only",
        action="store_true",
        help="Replay only the previously observed failure, not query/batch grids",
    )
    run(p.parse_args())


if __name__ == "__main__":
    main()
