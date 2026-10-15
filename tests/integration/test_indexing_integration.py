"""Real isolated PostgreSQL/Mongo artifacts; semantic model is explicitly a test double."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Event

import pytest
from conftest import ingest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.index_store import MongoIndexStore
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.main import create_app

pytestmark = pytest.mark.postgres


class ModelDouble:
    model_id, version, dimension = "integration-test-double", "v1", 3

    def available(self):
        return True

    def prepare(self):
        pass

    def embed(self, text):
        return [0.1, 0.2, 0.3]


@pytest.fixture
def index_context(pg_container, mongo_test_database):
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000, timeoutMS=10000)
    db = client[mongo_test_database[1]]
    initialize(db, apply=True)
    c = pg_container
    c.settings.admin_enabled = True
    c.mongo_projector.documents = MongoContentStore(db, invalidate_content_bags=False)
    index = IndexingService(c.uow, MongoIndexStore(db), ModelDouble(), c.settings)
    c.indexing = index
    c.processing_finalizer.on_content_ready = index.enqueue_auto
    try:
        yield c, db, index
    finally:
        client.close()


def finish(c):
    version = ingest(c, ["page one with text", "page two with text"])
    c.ocr.create_job(version["id"])
    while c.worker.once("integration-ocr-index"):
        pass
    return version


def test_real_index_api_worker_consistency(index_context):
    c, db, index = index_context
    version = finish(c)
    preserved = {name: list(db[name].find({})) for name in
                 ("users", "learning_resources", "ocr_pages", "mongo_content_units", "content_bags")}
    with TestClient(create_app(c)) as api:
        base = f"/api/v1/resource-versions/{version['id']}"
        assert api.get(base + "/index-status").json()["index_status"] == "NOT_STARTED"
        first = api.post(base + "/index")
        assert first.status_code == 202
        assert c.worker.once("integration-index")
        status = api.get(base + "/index-status").json()
        assert status["index_status"] == "READY" and status["chunk_count"] == 2
        chunks = api.get(base + "/chunks?limit=1").json()
        assert len(chunks["items"]) == 1
        assert chunks["index_run_id"] == first.json()["index_run_id"]
        assert chunks["items"][0]["vector_ready"]
        assert "embedding" not in chunks["items"][0]
        assert api.post(base + "/index").status_code == 200
        rebuilt = api.post(base + "/reindex")
        assert rebuilt.status_code == 202
        assert api.post(base + "/reindex").status_code == 409
        assert c.worker.once("integration-reindex")
        status = api.get(base + "/index-status").json()
        assert status["published_run_id"] == rebuilt.json()["index_run_id"]
        assert status["index_status"] == "READY"
        assert api.get("/api/v1/resource-versions/missing/chunks").status_code == 404
    assert all(list(db[name].find({})) == docs for name, docs in preserved.items())
    assert initialize(db)["ready"]
    docs = list(db.indexed_chunks.find({}))
    assert len(docs) == 4
    assert sum(doc["index_run_id"] == status["published_run_id"] for doc in docs) == 2
    assert all(sum(doc["bow_tf"].values()) == doc["bow_length"] for doc in docs)
    assert all(not doc["retrieval_eligible"] for doc in docs)


def test_real_worker_lock_recovery_and_failed_rebuild_preserves_history(index_context):
    c, db, index = index_context
    version = finish(c)
    first = index.enqueue(version["id"])
    entered, release = Event(), Event()
    original = index.embedding.embed

    def slow(text):
        entered.set()
        assert release.wait(15)
        return original(text)

    index.embedding.embed = slow
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(index.once)
            assert entered.wait(10)
            assert not index.once()
            assert index.enqueue(version["id"])["index_run_id"] == first["index_run_id"]
            with pytest.raises(IndexingError, match="INDEX_ALREADY_RUNNING"):
                index.enqueue(version["id"], force=True)
            release.set()
            assert future.result(timeout=15)
    finally:
        release.set()
    saved = deepcopy(list(db.indexed_chunks.find({})))
    second = index.enqueue(version["id"], force=True)
    with c.uow() as repo:
        repo.update("etl_stage_runs", second["index_run_id"], {"status": "RUNNING"})

    def fail(text):
        raise RuntimeError("private model error")

    index.embedding.embed = fail
    assert index.once()
    status = index.status(version["id"])
    assert status["index_status"] == "FAILED"
    assert status["published_run_id"] == first["index_run_id"]
    assert list(db.indexed_chunks.find({})) == saved
    assert index.chunks(version["id"])["total"] == 2


def test_auto_end_to_end_uses_existing_worker(index_context):
    c, db, index = index_context
    c.settings.index_auto_enabled = True
    c.sync.run(full=True)
    with c.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 0
    version = finish(c)
    assert index.status(version["id"])["index_status"] == "READY"
    assert db.indexed_chunks.count_documents({"resource_version_id": version["id"]}) == 2
    with c.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 1
    assert not c.worker.once("integration-idle")
