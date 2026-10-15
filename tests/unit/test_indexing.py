from copy import deepcopy

import pytest
from conftest import completed, ingest
from fastapi.testclient import TestClient

from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.domain.indexing import (
    IndexingError,
    bag_of_words,
    digest,
    validate_vector,
    word_windows,
)
from data_sync_etl.main import create_app


class TestEmbedding:
    __test__ = False
    model_id, version, dimension = "test-only-semantic-mock", "v1", 3
    enabled = True

    def available(self):
        return self.enabled

    def ready(self):
        return self.enabled

    def prepare(self):
        if not self.enabled:
            raise IndexingError("BLOCKED_EMBEDDING_RUNTIME", 503)

    def embed(self, text):
        # Explicit test double; never composed in the application runtime.
        return [0.1, 0.2, 0.3]


class MemoryIndexStore:
    def __init__(self, documents):
        self.documents, self.rows = documents, {}

    def ensure_ready(self):
        pass

    def source_units(self, version_id, limit):
        return [deepcopy(doc) for doc in self.documents.units.values()
                if doc["resource_version_id"] == version_id and doc.get("is_current")][:limit]

    def upsert(self, document):
        self.rows[document["_id"]] = deepcopy(document)

    def artifacts(self, version_id, run_id, limit, offset=0):
        return sorted([deepcopy(doc) for doc in self.rows.values() if doc["resource_version_id"] == version_id
                       and doc["index_run_id"] == run_id], key=lambda doc: doc["chunk_sequence"])[offset:offset + limit]


def setup_index(container):
    service = IndexingService(container.uow, MemoryIndexStore(container.documents), TestEmbedding(), container.settings)
    container.indexing = service
    container.processing_finalizer.on_content_ready = service.enqueue_auto
    container.settings.admin_enabled = True
    return service


def test_word_windows_unicode_offsets_overlap():
    text = "  Tiếng Việt có dấu\n học liệu số và English!   cuối "
    chunks = list(word_windows(text, 4, 1))
    assert all(text[a:b] == value for a, b, value in chunks)
    assert chunks[-1][2].endswith("cuối")
    assert chunks[0][2].split()[-1] == chunks[1][2].split()[0]
    assert list(word_windows("", 4, 1)) == []
    assert len(list(word_windows("ngắn", 4, 1))) == 1
    assert len(list(word_windows("a b c d e f g", 4, 1))) == 2
    with pytest.raises(ValueError):
        list(word_windows(text, 4, 4))
    tf = bag_of_words("HỌC học, Vie\u0323̂t! Việt _ +")
    assert tf["học"] == 2
    assert sum(tf.values()) == 4


@pytest.mark.parametrize("vector", [[0, 0, 0], [1, float("nan"), 1], [1, float("inf"), 1], [1], [True, 1, 1]])
def test_invalid_vectors(vector):
    with pytest.raises(IndexingError):
        validate_vector(vector, 3)


def test_index_api_queue_worker_idempotent(container):
    version, _ = completed(container)
    index = setup_index(container)
    before = deepcopy(container.documents.units)
    with TestClient(create_app(container)) as client:
        path = f"/api/v1/resource-versions/{version['id']}/index"
        response = client.post(path)
        assert response.status_code == 202, response.text
        run_id = response.json()["index_run_id"]
        assert client.post(path).json()["index_run_id"] == run_id
        assert not index.store.rows
        assert container.worker.once("index-worker")
        assert index.status(version["id"])["index_status"] == "READY"
        assert client.post(path).status_code == 200
        assert client.post(path).json()["action"] == "ALREADY_INDEXED"
        assert len(index.store.rows) > 0
        assert all(not doc["retrieval_eligible"] and doc["review_status"] == "UNREVIEWED" for doc in index.store.rows.values())
        assert client.post("/api/v1/resource-versions/missing/index").status_code == 404
    assert before == container.documents.units


def test_api_missing_model_and_content_not_ready(container):
    version = ingest(container)
    index = setup_index(container)
    with TestClient(create_app(container)) as client:
        path = f"/api/v1/resource-versions/{version['id']}/index"
        response = client.post(path)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "CONTENT_NOT_READY"
        container.ocr.create_job(version["id"])
        while container.worker.once("test"):
            pass
        index.embedding.enabled = False
        response = client.post(path)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "BLOCKED_EMBEDDING_RUNTIME"
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 0


def test_failures_never_publish_partial_artifacts(container):
    version, _ = completed(container)
    index = setup_index(container)
    run = index.enqueue(version["id"])
    original = index.store.upsert

    def partial(doc):
        original(doc)
        raise RuntimeError("secret model path must not leak")

    index.store.upsert = partial
    assert index.once()
    status = index.status(version["id"])
    assert status["index_status"] == "FAILED"
    assert status["error_code"] == "INDEX_FAILED"
    assert status["published_run_id"] is None
    assert index.chunks(version["id"])["items"] == []
    with pytest.raises(IndexingError, match="REINDEX_REQUIRED"):
        index.enqueue(version["id"])
    assert index.store.artifacts(version["id"], run["index_run_id"], 100)


def test_source_change_blocks_publication_and_limits(container):
    version, _ = completed(container)
    index = setup_index(container)
    index.enqueue(version["id"])
    original = index.store.upsert

    def mutate(doc):
        original(doc)
        with container.uow() as repo:
            repo.update("content_unit_texts", doc["content_unit_id"], {"text": "changed"})

    index.store.upsert = mutate
    assert index.once()
    with container.uow() as repo:
        assert index._runs(repo, version["id"])[0]["status"] == "FAILED"
    assert index.chunks(version["id"])["items"] == []


def test_inactive_and_model_loss_and_recovery(container):
    version, _ = completed(container)
    index = setup_index(container)
    response = index.enqueue(version["id"])
    with container.uow() as repo:
        repo.update("etl_stage_runs", response["index_run_id"], {"status": "RUNNING"})
    # An abandoned RUNNING row resumes only after acquiring the existing version lock.
    assert index.once()
    index.embedding.enabled = False
    assert index.status(version["id"])["index_status"] == "BLOCKED_RUNTIME"
    assert not index.status(version["id"])["vector_ready"]
    with container.uow() as repo:
        repo.update("master_learning_resources", version["resource_id"], {"is_active": False})
    with pytest.raises(IndexingError) as exc:
        index.status(version["id"])
    assert exc.value.status == 404


def test_fingerprint_config_flags_and_generation(container):
    version, _ = completed(container)
    index = setup_index(container)
    with container.uow() as repo:
        before = index._snapshot(repo, version["id"])[3]
    container.settings.chunk_size_words = 201
    with container.uow() as repo:
        assert index._snapshot(repo, version["id"])[3] != before
    assert digest(["x", 1]) == digest(["x", 1])


def test_production_adapter_is_local_only(tmp_path):
    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding

    model = LocalSentenceEmbedding(tmp_path / "missing", "model", "v1", 384)
    assert not model.available()
    with pytest.raises(IndexingError, match="BLOCKED_EMBEDDING_RUNTIME"):
        model.prepare()


def test_chunks_api_pagination_filter_stale_and_no_raw_vectors(container):
    version, _ = completed(container)
    index = setup_index(container)
    base = f"/api/v1/resource-versions/{version['id']}"
    with TestClient(create_app(container)) as client:
        assert client.get(base + "/chunks").json()["items"] == []
        index.enqueue(version["id"])
        assert client.get(base + "/chunks").json()["items"] == []
        assert index.once()
        response = client.get(base + "/chunks?limit=1&offset=1")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert len(data["items"]) == 1
        chunk = data["items"][0]
        assert chunk["chunk_sequence"] == 2
        assert "embedding" not in chunk and "bow_tf" not in chunk
        assert client.get(base + "/chunks?offset=100").json()["items"] == []
        unit = chunk["content_unit_id"]
        assert client.get(base + f"/chunks?content_unit_id={unit}").json()["total"] == 1
        assert client.get(base + "/chunks?content_unit_id=other").json()["items"] == []
        assert client.get(base + "/chunks?limit=101").status_code == 422
        assert client.get(base + "/chunks?offset=-1").status_code == 422
        assert client.get("/api/v1/resource-versions/missing/chunks").status_code == 404
        container.settings.chunk_size_words += 1
        stale = client.get(base + "/chunks").json()
        assert stale["index_status"] == "STALE" and stale["items"] == []


def test_chunks_mongo_failure_and_tampered_artifact(container):
    version, _ = completed(container)
    index = setup_index(container)
    index.enqueue(version["id"])
    index.once()
    doc = next(iter(index.store.rows.values()))
    doc["bow_length"] += 1
    with TestClient(create_app(container)) as client:
        path = f"/api/v1/resource-versions/{version['id']}/chunks"
        assert client.get(path).status_code == 503

        def unavailable(*args):
            raise RuntimeError("private Mongo URI")

        index.store.source_units = unavailable
        response = client.get(path)
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "INDEX_UNAVAILABLE"
        assert "private" not in response.text


def test_status_api_transitions_and_capabilities(container):
    version = ingest(container)
    index = setup_index(container)
    path = f"/api/v1/resource-versions/{version['id']}/index-status"
    with TestClient(create_app(container)) as client:
        assert client.get(path).json()["index_status"] == "CONTENT_NOT_READY"
        container.ocr.create_job(version["id"])
        while container.worker.once("test"):
            pass
        assert client.get(path).json()["index_status"] == "NOT_STARTED"
        run = index.enqueue(version["id"])
        assert client.get(path).json()["index_status"] == "PENDING"
        with container.uow() as repo:
            repo.update("etl_stage_runs", run["index_run_id"], {"status": "RUNNING"})
        assert client.get(path).json()["index_status"] == "RUNNING"
        index.once()
        data = client.get(path).json()
        assert data["index_status"] == "READY" and data["processing_status"] == "CONTENT_READY"
        assert data["vector_ready"] and data["bow_ready"] and data["chunk_count"] == 3
        container.settings.chunk_overlap_words += 1
        assert client.get(path).json()["index_status"] == "STALE"
        index.embedding.enabled = False
        blocked = client.get(path).json()
        assert blocked["index_status"] == "BLOCKED_RUNTIME" and not blocked["vector_ready"]
        assert client.get("/api/v1/resource-versions/missing/index-status").status_code == 404
        invalid = client.get(f"/api/v1/resource-versions/{'x' * 31}/index-status")
        assert invalid.status_code == 422 and invalid.json()["detail"]["phase"] == "indexing"
        capabilities = client.get("/api/v1/system/capabilities").json()
        assert capabilities["processing"]["chunking"]["enabled"]
        assert capabilities["representations"]["bag_of_words"]
        assert not capabilities["representations"]["vector"]["semantic"]
        assert not capabilities["retrieval"]["modes"]


@pytest.mark.parametrize("enabled,model,expected", [(False, True, None), (True, True, "COMPLETED"), (True, False, "FAILED")])
def test_auto_only_at_new_content_ready_and_no_backfill(container, enabled, model, expected):
    index = setup_index(container)
    index.embedding.enabled = model
    container.settings.index_auto_enabled = enabled
    # Master metadata sync alone creates no index stage.
    container.sync.run(full=True)
    with container.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 0
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    while container.worker.once("auto-test"):
        pass
    with container.uow() as repo:
        stages = index._runs(repo, version["id"])
        assert (stages[0]["status"] if stages else None) == expected
        from data_sync_etl.application.processing_state import processing_state

        assert processing_state(repo, repo.get("ocr_jobs", job["id"])) == "CONTENT_READY"
    # Enabling later does not sweep historical CONTENT_READY versions.
    container.settings.index_auto_enabled = True
    for _ in range(3):
        container.worker.once("auto-test")
    with container.uow() as repo:
        assert len(index._runs(repo, version["id"])) == len(stages)


def test_auto_queue_same_transaction_as_projection(container, monkeypatch):
    index = setup_index(container)
    container.settings.index_auto_enabled = True
    version = ingest(container)
    job = container.ocr.create_job(version["id"])
    captured = []

    def callback(repo, version_id):
        projections = repo.find("etl_stage_runs", {"stage": "MONGO_PROJECTION", "resource_version_id": version_id})
        assert projections[-1]["status"] == "COMPLETED"
        index.enqueue_auto(repo, version_id)
        captured.append(repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}))

    monkeypatch.setattr(container.processing_finalizer, "on_content_ready", callback)
    while container.worker.once("auto-test"):
        pass
    assert captured == [1]
    assert index.status(version["id"])["index_status"] == "READY"
    assert container.processing_finalizer.finalize(job["id"])["status"] == "ALREADY_COMPLETED"
    assert captured == [1]


def test_reindex_api_keeps_completed_run_and_never_restarts_ocr(container):
    version, job = completed(container)
    index = setup_index(container)
    original_units = deepcopy(container.documents.units)
    with TestClient(create_app(container)) as client:
        base = f"/api/v1/resource-versions/{version['id']}"
        first = client.post(base + "/index").json()
        index.once()
        second = client.post(base + "/reindex", json={})
        assert second.status_code == 202, second.text
        assert second.json()["index_run_id"] != first["index_run_id"]
        assert client.post(base + "/reindex").status_code == 409
        assert client.get(base + "/index-status").json()["published_run_id"] == first["index_run_id"]
        assert index.once()
        status = client.get(base + "/index-status").json()
        assert status["index_status"] == "READY"
        assert status["published_run_id"] == second.json()["index_run_id"]
        assert client.post(base + "/reindex", json={"chunk_size_words": 1}).status_code == 422
        assert client.post("/api/v1/resource-versions/missing/reindex").status_code == 404
        index.embedding.enabled = False
        assert client.post(base + "/reindex").status_code == 503
    with container.uow() as repo:
        assert repo.count("ocr_jobs", {}) == 1
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED"
        assert len(index._runs(repo, version["id"])) == 2
    assert container.documents.units == original_units


def test_reindex_failure_preserves_previous_valid_generation(container):
    version, _ = completed(container)
    index = setup_index(container)
    first = index.enqueue(version["id"])
    index.once()
    saved = deepcopy(index.store.rows)
    second = index.enqueue(version["id"], force=True)
    index.embedding.embed = lambda text: [0, 0, 0]
    index.once()
    status = index.status(version["id"])
    assert status["index_status"] == "FAILED"
    assert status["published_run_id"] == first["index_run_id"]
    assert index.chunks(version["id"])["total"] == len(saved)
    assert index.store.rows == saved
    index.embedding.embed = lambda text: [0.1, 0.2, 0.3]
    third = index.enqueue(version["id"], force=True)
    assert third["index_run_id"] != second["index_run_id"]
    index.once()
    assert index.status(version["id"])["published_run_id"] == third["index_run_id"]


def test_chunk_limits_and_deadline_fail_without_partial_publication(container, monkeypatch):
    version, _ = completed(container)
    index = setup_index(container)
    container.settings.index_max_chunks = 1
    index.enqueue(version["id"])
    index.once()
    with container.uow() as repo:
        run = index._runs(repo, version["id"])[0]
    assert run["status"] == "FAILED" and run["error_message"] == "INDEX_LIMIT_EXCEEDED"
    assert index.chunks(version["id"])["items"] == []
    container.settings.index_max_chunks = 100
    index.enqueue(version["id"], force=True)
    clock = iter([0, 0, 1000])
    monkeypatch.setattr("data_sync_etl.application.indexing.monotonic", lambda: next(clock))
    index.once()
    assert index.status(version["id"])["index_status"] == "FAILED"


def test_index_isolation_and_active_resource_guards(container):
    first, _ = completed(container)
    index = setup_index(container)
    index.enqueue(first["id"])
    index.once()
    with container.uow() as repo:
        other = repo.insert("resource_versions", {
            **{k: v for k, v in first.items() if k not in ("id", "mongo_id")},
            "version_number": 2, "parent_version_id": first["id"],
        })
    assert index.chunks(other["id"])["items"] == []
    with container.uow() as repo:
        repo.update("master_learning_resources", first["resource_id"], {"is_active": False})
    with TestClient(create_app(container)) as client:
        for suffix in ("chunks", "index-status"):
            assert client.get(f"/api/v1/resource-versions/{first['id']}/{suffix}").status_code == 404
        for suffix in ("index", "reindex"):
            assert client.post(f"/api/v1/resource-versions/{first['id']}/{suffix}").status_code == 404


def test_openapi_exposes_exact_four_indexing_routes(container):
    paths = create_app(container).openapi()["paths"]
    for suffix, method in (("index", "post"), ("chunks", "get"), ("index-status", "get"), ("reindex", "post")):
        operation = paths[f"/api/v1/resource-versions/{{id}}/{suffix}"][method]
        assert operation["tags"] == ["Data Chunking and Representation"]
        assert "503" in operation["responses"] and "422" in operation["responses"]
    assert not any("resouce-versions" in path for path in paths)


def test_local_embedding_probe_does_not_download_and_rejects_dimension(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding

    (tmp_path / "modules.json").write_text('[{"path":""}]', encoding="utf-8")
    (tmp_path / "etl-model-revision.txt").write_text("revision-1", encoding="utf-8")
    (tmp_path / "model.safetensors").write_bytes(b"test fixture only, not a real model")
    monkeypatch.setattr("data_sync_etl.adapters.embeddings.find_spec", lambda _: True)
    calls = []

    def construct(path, **kwargs):
        calls.append(kwargs)
        assert path == str(tmp_path.resolve())
        assert kwargs == {"local_files_only": True, "device": "cpu"}
        return SimpleNamespace(get_sentence_embedding_dimension=lambda: 3,
                               encode=lambda *args, **kw: SimpleNamespace(tolist=lambda: [0.1, 0.2, 0.3]))

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=construct))
    model = LocalSentenceEmbedding(tmp_path, "test-only", "revision-1", 3)
    assert calls == [] and model.available()
    assert model.ready() and model.ready()
    assert len(calls) == 1
    assert model.embed("Tiếng Việt") == [0.1, 0.2, 0.3]
    wrong = LocalSentenceEmbedding(tmp_path, "test-only", "revision-1", 2)
    assert not wrong.ready()


def test_artifact_verification_pages_vectors_and_preserves_offsets(container):
    version, _ = completed(container)
    index = setup_index(container)
    container.settings.chunk_size_words = 20
    container.settings.chunk_overlap_words = 0
    text = "Học liệu tiếng Việt\n" * 600
    with container.uow() as repo:
        unit = repo.find("content_units", {"resource_version_id": version["id"]}, order=("sequence_no",))[0]
        repo.update("content_unit_texts", unit["id"], {"text": text})
    index.store.documents.units[unit["id"]]["text"] = text
    original = index.store.artifacts
    offsets = []

    def pages(version_id, run_id, limit, offset=0):
        assert limit <= 100
        offsets.append(offset)
        return original(version_id, run_id, limit, offset)

    index.store.artifacts = pages
    index.enqueue(version["id"])
    index.once()
    assert index.status(version["id"])["index_status"] == "READY"
    assert 100 in offsets
    chunks = index.chunks(version["id"], offset=100)
    assert len(chunks["items"]) == 10
    for chunk in chunks["items"]:
        assert text[chunk["char_start"]:chunk["char_end"]] == chunk["text"]
