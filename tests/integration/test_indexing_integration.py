"""Isolated real PG/Mongo persistence; the semantic model here is explicitly a double."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from conftest import ingest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import contracts, initialize
from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.main import create_app
from tests.indexing_support import ModelDouble, structured_projection

pytestmark = pytest.mark.postgres


@pytest.fixture
def index_context(pg_container, mongo_test_database):
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000, timeoutMS=10000)
    db = client[mongo_test_database[1]]
    assert initialize(db, apply=True, dimension=3)["ready"]
    c = pg_container
    c.settings.admin_enabled = True
    c.settings.index_embedding_dimension = 3
    c.mongo_projector.documents = MongoContentStore(db)
    index = IndexingService(c.uow, MongoContentReader(db), ModelDouble(), c.settings, bags=MongoBagStore(db, 3))
    c.indexing = index
    c.processing_finalizer.on_content_ready = index.enqueue_auto
    try:
        yield c, db, index
    finally:
        client.close()


def finish(c, structured=True):
    if structured:
        structured_projection(c)
    version = ingest(c, ["Học sinh nghiên cứu định luật vật lý", "Bài giảng toán học phương trình"])
    c.ocr.create_job(version["id"])
    while c.worker.once("integration-ocr-index"):
        pass
    return version


def test_real_index_api_worker_consistency(index_context):
    c, db, index = index_context
    version = finish(c)
    pages = list(db.ocr_pages.find({}))
    with TestClient(create_app(c)) as api:
        base = f"/api/v1/resource-versions/{version['id']}"
        preview = api.get(base + "/chunks").json()
        assert preview["total"] == 2 and preview["representation_status"] == "PREVIEW"
        first = api.post(base + "/index")
        assert first.status_code == 202, first.text
        assert c.worker.once("integration-index")
        status = api.get(base + "/index-status").json()
        assert status["index_status"] == "READY" and status["bag_count"] == 1
        assert api.post(base + "/index").status_code == 200
        assert "embedding" not in status["bags"][0]
        chunks = api.get(base + "/chunks?offset=1&limit=1").json()
        assert chunks["total"] == 2 and len(chunks["items"]) == 1
        assert "vector_ready" not in chunks["items"][0]
        doc = db.content_bags.find_one({})
        assert doc["embedding_text"] == " | ".join(doc["keywords"])
        assert doc["embedding_model"] == "test-double-only@v1"
        with c.uow() as repo:
            assert repo.get("content_units", doc["_id"])["unit_type"] == "CHAPTER"
        assert api.post(base + "/reindex").status_code == 202
        assert api.get(base + "/index-status").json()["published_run_id"] is None
        assert c.worker.once("reindex-test")
        assert db.content_bags.count_documents({}) == 1
        assert index.status(version["id"])["index_status"] == "READY"
    assert pages == list(db.ocr_pages.find({}))
    assert set(db.list_collection_names()) == set(contracts(3))
    assert initialize(db, dimension=3)["ready"]


def test_real_lock_restart_and_failed_rebuild(index_context):
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
    second = index.enqueue(version["id"], force=True)
    with c.uow() as repo:
        repo.update("etl_stage_runs", second["index_run_id"], {"status": "RUNNING"})
    def fail(text):
        raise RuntimeError("private model error")
    index.embedding.embed = fail
    assert index.once()
    assert index.status(version["id"])["index_status"] == "FAILED"
    assert index.status(version["id"])["published_run_id"] is None
    assert index.chunks(version["id"])["total"] == 2
    assert db.content_bags.count_documents({}) == 1
    index.embedding.embed = original
    index.enqueue(version["id"], force=True)
    index.once()
    assert index.status(version["id"])["index_status"] == "READY"


@pytest.mark.parametrize("structured", [False, True])
def test_auto_and_unstructured_content_ready(index_context, structured):
    c, db, index = index_context
    c.settings.index_auto_enabled = True
    version = finish(c, structured)
    status = index.status(version["id"])
    assert status["processing_status"] == "CONTENT_READY"
    assert status["index_status"] == ("READY" if structured else "BLOCKED_STRUCTURE")
    assert index.chunks(version["id"])["total"] == 2
    assert db.content_bags.count_documents({}) == int(structured)
    with c.uow() as repo:
        assert len(index._runs(repo, version["id"])) == 1
        index.enqueue_auto(repo, version["id"])
        assert len(index._runs(repo, version["id"])) == 1
    assert not c.worker.once("idle")
    assert set(db.list_collection_names()) == set(contracts(3))
