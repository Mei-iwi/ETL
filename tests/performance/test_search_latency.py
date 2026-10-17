"""Offline reproducible orchestration benchmark; real model is explicitly opt-in.

No dotenv/Dev connections. All writes are to the normal temporary test fixture.
Stage timings are inclusive: never add nested durations to calculate total.
"""
import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
from collections import defaultdict
from time import perf_counter

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.storage.evaluation_store import EvaluationFileStore
from data_sync_etl.application.evaluation import EvaluationService
from data_sync_etl.domain.evaluation import checksum
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.conftest import make_pdf
from tests.local_admin_client import TestClient
from tests.unit.test_search import searchable as searchable

QUERIES = (
    "page text", "math", "science", "learning", "phân số", "học liệu",
    "tiếng Việt", "định luật", "lịch sử", "toán học", "không có kết quả xyz",
    "  page text  ", "Vie\u0323̂t", "cộng trừ", "page text", "math", "science", "học liệu",
)
ACCESS = SearchAccess("local_demo")

VIETNAMESE = (
    'học liệu', 'tiếng Việt', 'phân số', 'định luật', 'lịch sử', 'toán học',
    'cộng trừ', 'nhân chia', 'địa lý', 'sinh học', 'hóa học', 'vật lý',
    'lớp một', 'LỚP HAI', '  học liệu  ', 'Việ̂t', 'Vie\u0323̂t', 'a\u0301',
    'á', 'Đặng', 'Nguyễn', 'trường học 🏫', 'dòng\nmới', 'tri thức',
)


def summary(values):
    ordered = sorted(values)
    position = (len(ordered) - 1) * .95
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return {"n": len(values), "min_ms": min(values), "median_ms": statistics.median(values),
            "p95_ms": ordered[low] + (ordered[high] - ordered[low]) * (position - low),
            "max_ms": max(values)}


class Probe:
    def __init__(self, monkeypatch):
        self.monkeypatch = monkeypatch
        self.rows = defaultdict(list)

    def wrap(self, obj, name, stage):
        original = getattr(obj, name)
        def measured(*args, **kwargs):
            started = perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                self.rows[stage].append((perf_counter() - started) * 1000)
        self.monkeypatch.setattr(obj, name, measured)

    def reset(self):
        self.rows.clear()

    def snapshot(self):
        return {name: {**summary(values), "total_ms": sum(values)} for name, values in self.rows.items()}


def observed(result):
    return {"total": result["total"], "items": [{key: row[key] for key in (
        "resource_id", "resource_version_id", "bag_id", "score", "score_components")}
        for row in result["items"]]}


def build_corpus(c):
    versions = []
    with c.uow() as repo:
        template = repo.get("master_learning_resources", "resource-1")
    for number in range(1, 6):
        resource_id = f"benchmark-resource-{number}"
        with c.uow() as repo:
            repo.insert("master_learning_resources", {**template, "id": resource_id, "mongo_id": None,
                "source_origin": "LOCAL", "title": f"Benchmark fixture {number}"})
        path = make_pdf(c, [f"Page text math science learning topic {number}"] * 2, name=f"bench-{number}.pdf")
        version = c.ingestion.run(resource_id, "input", path.name)["version"]
        c.ocr.create_job(version["id"])
        while c.worker.once("benchmark-fixture"):
            pass
        c.indexing.enqueue(version["id"])
        assert c.indexing.once()
        versions.append(version["id"])
    return versions


def test_search_and_evaluation_latency(searchable, tmp_path, monkeypatch):
    c, first = searchable
    real = os.environ.get("ETL_PERF_REAL_MODEL") == "1"
    versions = [first["id"], *build_corpus(c)]
    cold = {"status": "NOT_MEASURED", "reason": "orchestration test double; not a model cold-load measurement"}
    if real:
        from data_sync_etl.config import Settings
        settings = Settings(_env_file=None)
        model = LocalSentenceEmbedding(settings.index_embedding_model_path, settings.index_embedding_model_id,
                                       settings.index_embedding_model_version, settings.index_embedding_dimension)
        assert model.available(), "Explicit offline local model configuration required"
        started = perf_counter()
        model.prepare()
        cold = {"status": "MEASURED", "ms": (perf_counter() - started) * 1000,
                "scope": "first prepare in this process, imports + weights + probe; OS file cache uncontrolled"}
        c.indexing.embedding = model
        c.settings.index_embedding_dimension = model.dimension
        c.indexing.bags.dimension = model.dimension
        for version_id in versions:
            c.indexing.enqueue(version_id, force=True)
            assert c.indexing.once()
        assert all(len(b.embedding) == 384 for b in c.indexing.bags.rows.values())
        # Untimed real-model Unicode/finite-vector regression, not fabricated
        # vectors and not mixed into cold or warm latency samples.
        from data_sync_etl.domain.indexing import validate_vector
        for query in VIETNAMESE:
            assert len(validate_vector(model.embed(query), 384)) == 384
    evaluation = EvaluationService(c.search, EvaluationFileStore(tmp_path / "performance-evaluation"), c.settings)
    probe = Probe(monkeypatch)
    for obj, method, stage in (
        (c.indexing.embedding, "prepare", "model.prepare"), (c.indexing.embedding, "available", "model.readiness"),
        (c.indexing.embedding, "embed", "model.embed"), (c.search, "_metadata", "pg.eligibility"),
        (c.search, "_ready_snapshot", "pg.snapshot_guard"), (c.search, "_snippets", "rank.snippets"),
        (c.indexing.bags, "search_candidates", "mongo.candidates_double"), (c.indexing.bags, "get_bag", "mongo.get_bag_double"),
        (evaluation, "corpus", "evaluation.corpus_guard"), (evaluation, "corpus_marker", "pg.corpus_marker"),
    ):
        probe.wrap(obj, method, stage)
    if real:
        probe.wrap(c.indexing.embedding, "_encode", "model.tokenize_encode")
    method_samples = defaultdict(list)
    outputs = {}
    # Warm-up is reported independently and never contributes to warm medians.
    for method in ("bow", "vector", "hybrid"):
        c.search.search("page text", method, size=5)
    warmup = probe.snapshot()
    probe.reset()
    repetitions = int(os.environ.get("ETL_PERF_REPEATS", "3"))
    assert 2 <= repetitions <= 20
    for _ in range(repetitions):
        for query_index, query in enumerate(QUERIES):
            for method in ("bow", "vector", "hybrid"):
                started = perf_counter()
                result = c.search.search(query, method, size=5)
                method_samples[method].append((perf_counter() - started) * 1000)
                key = f"{query_index}:{method}"
                value = observed(result)
                if key in outputs:
                    assert outputs[key] == value
                outputs[key] = value
                for k in (1, 3):
                    assert observed(c.search.search(query, method, size=k))["items"] == value["items"][:k]
    search_stages = probe.snapshot()
    command = {"name": "Synthetic performance fixture; quality not human confirmed", "ground_truth_confirmed": False,
               "filters": {}, "queries": [{"query_id": f"q{i}", "query": query,
                    "relevance": [{"item_id": first["resource_id"], "grade": 1}]} for i, query in enumerate(QUERIES)]}
    dataset = evaluation.create_dataset(command, ACCESS)
    run_command = {"dataset_id": dataset["dataset_id"], "methods": ["bow", "vector", "hybrid"],
                   "k_values": [1, 3, 5], "max_results": 5, "warmup_queries": 0}
    probe.reset()
    started = perf_counter()
    run = evaluation.run(run_command, ACCESS)
    evaluation_ms = (perf_counter() - started) * 1000
    assert run["status"] == "COMPLETED" and len(run["observations"]) == 54
    assert "LABELS_NOT_HUMAN_CONFIRMED" in run["warnings"]
    eval_stages = probe.snapshot()
    eval_latencies = {method: summary([row["latency_ms"] for row in run["observations"] if row["method"] == method])
                      for method in ("bow", "vector", "hybrid")}
    paired = None
    if os.environ.get('ETL_PERF_COMPARE_BASE') == '1':
        from tests.performance.baseline_compare import compare
        paired = compare(c, evaluation, command, run_command, QUERIES, tmp_path, monkeypatch, Probe, summary, observed)
    probe.reset()
    api_samples = []
    serialize_samples = []
    with TestClient(create_app(c)) as client:
        for _ in range(5):
            started = perf_counter()
            response = client.post("/api/v1/search", json={"query": "page text", "method": "hybrid", "size": 5})
            assert response.status_code == 200
            api_samples.append((perf_counter() - started) * 1000)
            started = perf_counter()
            json.dumps(response.json())
            serialize_samples.append((perf_counter() - started) * 1000)
    # Permission still runs before model work, including when a repeated query is warm.
    probe.reset()
    assert c.search.search("page text", "vector", access=SearchAccess())["items"] == []
    assert not probe.rows.get("model.embed")
    source = source_hashes()
    result = {"schema_version": 1, "label": os.environ.get("ETL_PERF_LABEL", "baseline"),
        "environment": {"python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
            "device": "cpu", "processes": 1, "omp_threads": os.environ.get("OMP_NUM_THREADS"),
            "packages": {name: importlib.metadata.version(name) for name in ("SQLAlchemy", "torch", "sentence-transformers")}},
        "model": {"mode": "real_local" if real else "ORCHESTRATION_DOUBLE_ONLY", "id": c.indexing.embedding.model_id,
            "revision": c.indexing.embedding.version, "dimension": c.indexing.embedding.dimension},
        "fixture": {"versions": len(versions), "candidate_bags": len(c.indexing.bags.rows), "queries": len(QUERIES),
            "query_dataset_hash": checksum(QUERIES), "query_hashes": [checksum(q) for q in QUERIES],
            "ranked_result_hash": checksum(outputs), "corpus_snapshot_hash": run["corpus_fingerprint"]},
        "source_sha256": source, "cold_model_prepare": cold, "warmup_stages": warmup,
        "paired": paired,
        "search_warm": {method: summary(values) for method, values in method_samples.items()},
        "search_stages_including_prefix_checks": search_stages,
        "evaluation": {"total_ms": evaluation_ms, "per_method": eval_latencies, "stages": eval_stages,
                       "observations": 54, "k_values": [1, 3, 5], "quality": "NOT_RUN_UNCONFIRMED_GROUND_TRUTH"},
        "api_warm": summary(api_samples), "client_json_serialization": summary(serialize_samples),
        "not_measured": ["GPU", "multi_worker_RAM", "RAM_peak", "real_PG_Mongo_latency", "API_cold_total", "ranking_total", "tokenize_separate_from_encode"],
        "timing_note": "inclusive nested stage timings; do not sum; prefix checks excluded from search_warm latency samples"}
    output = os.environ.get("ETL_PERF_OUTPUT")
    if output:
        from pathlib import Path
        target = Path(output).resolve()
        assert target.is_relative_to(Path.cwd().resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert not real or cold["status"] == "MEASURED"


def source_hashes():
    from pathlib import Path
    return {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in (
        "src/data_sync_etl/adapters/embeddings.py", "src/data_sync_etl/application/search.py",
        "src/data_sync_etl/application/evaluation.py")}
