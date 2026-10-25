"""Compare old/new semantic validation + cosine on frozen real-model artifacts.

This excludes DB/auth/plan/hydration/HTTP and does not connect to any database.
"""

import argparse
import json
import math
import time
from pathlib import Path

from data_sync_etl.domain.evaluation import percentile
from data_sync_etl.domain.search import cosine
from data_sync_etl.domain.semantic import normalize_dense


def run(baseline, output):
    import numpy as np

    if not output.resolve().is_relative_to(Path(".test-tmp").resolve()):
        raise ValueError("Private output required")

    def read(pattern, max_rows):
        candidates = list(baseline.glob(pattern))
        if len(candidates) != 1:
            raise ValueError("One frozen artifact per strategy required")
        with np.load(candidates[0], allow_pickle=False) as data:
            values = data["vectors"].astype(float)
        if (
            values.ndim != 2
            or values.shape[1] != 1024
            or not 1 <= len(values) <= max_rows
            or not np.isfinite(values).all()
            or not np.allclose(np.linalg.norm(values, axis=1), 1.0, atol=1e-5)
        ):
            raise ValueError("Bounded normalized1024D frozen vectors required")
        return values.tolist()

    rows, queries = read("contextual-*.npz", 2000), read("queries-*.npz", 100)
    before, after = [], []
    error = 0.0
    same = True
    for query in queries:
        start = time.perf_counter()
        for row in rows:
            normalize_dense(row)
        left = [cosine(query, row) for row in rows]
        before.append((time.perf_counter() - start) * 1000)
        start = time.perf_counter()
        normalized_query = normalize_dense(query)
        normalized = [normalize_dense(row) for row in rows]
        right = [
            max(
                -1.0, min(1.0, math.fsum(a * b for a, b in zip(normalized_query, row, strict=True)))
            )
            for row in normalized
        ]
        after.append((time.perf_counter() - start) * 1000)
        error = max(error, max(abs(a - b) for a, b in zip(left, right, strict=True)))
        same &= sorted(range(len(left)), key=lambda i: (-left[i], i)) == sorted(
            range(len(right)), key=lambda i: (-right[i], i)
        )
    result = {
        "scope": "real_frozen_vectors_validation_plus_similarity_only_not_DB_or_HTTP",
        "queries": len(queries),
        "vectors_per_query": len(rows),
        "baseline_ms": {"p50": percentile(before, 0.5), "p95": percentile(before, 0.95)},
        "candidate_ms": {"p50": percentile(after, 0.5), "p95": percentile(after, 0.95)},
        "max_score_difference": error,
        "identical_chunk_order": same,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline-dir", type=Path, default=Path(".test-tmp/semantic-quality/experiment")
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".test-tmp/semantic-quality/optimization/cosine-before-after.json"),
    )
    args = parser.parse_args()
    run(args.baseline_dir, args.output)
