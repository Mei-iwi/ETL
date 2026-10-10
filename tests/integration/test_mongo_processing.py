import os
from uuid import uuid4

import pytest
from conftest import ingest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.main import create_app
from data_sync_etl.ports.processing import OCRResult


def test_real_mongo_projection_upserts_and_reconciliation(container):
    uri = os.environ.get("TEST_MONGO_URI")
    if not uri:
        pytest.skip("TEST_MONGO_URI not configured; real MongoDB test not run")
    name = "etl_test_" + uuid4().hex
    client = MongoClient(uri, serverSelectionTimeoutMS=3000, timeoutMS=10000)
    try:
        client.admin.command("ping")
        db = client[name]
        container.mongo_projector.documents = MongoContentStore(db)
        version = ingest(container)
        job = container.ocr.create_job(version["id"])
        for _ in range(3):
            container.ocr.succeed(container.ocr.claim("w"), OCRResult("Tiếng Việt\nDòng 2"))
        assert container.worker.once("projection")
        container.mongo_projector.project(version["id"], job["id"])
        assert db.ocr_pages.count_documents({}) == 3
        assert db.ocr_pages.find_one({})["raw_text"] == "Tiếng Việt\nDòng 2"
        assert db.mongo_content_units.count_documents({"is_current": True}) == 3
        # Reconciliation marks obsolete documents; it never deletes their content.
        store = container.mongo_projector.documents
        ids = [d["_id"] for d in db.mongo_content_units.find({})]
        store.reconcile_content_units(version["id"], ids[:2])
        assert db.mongo_content_units.count_documents({}) == 3
        assert db.mongo_content_units.count_documents({"is_current": False}) == 1
    finally:
        # Only the unique test database created above is ever removed.
        assert name.startswith("etl_test_") and len(name) == 41
        client.drop_database(name)
        client.close()


@pytest.mark.postgres
def test_process_api_worker_postgres_and_mongo(pg_container, mongo_test_database, monkeypatch):
    c = pg_container
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000)
    database = client[mongo_test_database[1]]
    c.mongo_projector.documents = MongoContentStore(database)
    c.settings.admin_enabled = True
    try:
        version = ingest(c, ["Native page one", "Native page two"])
        with TestClient(create_app(c)) as api:
            response = api.post(f"/api/v1/resource-versions/{version['id']}/process")
            assert response.status_code == 202
            job_id = response.json()["job_id"]
            with c.uow() as repo:
                assert repo.get("ocr_jobs", job_id)["status"] == "PENDING"
                assert repo.count("ocr_page_tasks", {"job_id": job_id}) == 2

            original = c.mongo_projector.documents.upsert_ocr_page
            failure = True

            def fail_once(document):
                nonlocal failure
                if failure:
                    failure = False
                    raise RuntimeError("temporary MongoDB outage")
                return original(document)

            monkeypatch.setattr(c.mongo_projector.documents, "upsert_ocr_page", fail_once)
            assert c.worker.once("integration")
            assert c.worker.once("integration")
            with c.uow() as repo:
                assert repo.get("ocr_jobs", job_id)["status"] == "COMPLETED"
                assert repo.count("ocr_page_results", {}) == 2
                stage = repo.find("etl_stage_runs", {"stage": "MONGO_PROJECTION"})[0]
                assert stage["status"] == "FAILED"
                repo.update(
                    "etl_stage_runs",
                    stage["id"],
                    {
                        "finished_at": stage["finished_at"].replace(year=2020),
                    },
                )
            assert c.worker.once("reconciler")
            assert c.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
            assert database.ocr_pages.count_documents({}) == 2
            assert database.mongo_content_units.count_documents({"is_current": True}) == 2
            with c.uow() as repo:
                for task in repo.find("ocr_page_tasks", {"job_id": job_id}):
                    result = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
                    page = database.ocr_pages.find_one({"_id": f"{job_id}:{task['page_num']}"})
                    assert page["raw_text"] == result["raw_text"]
                for unit in repo.find("content_units", {"resource_version_id": version["id"]}):
                    body = repo.get("content_unit_texts", unit["id"])
                    document = database.mongo_content_units.find_one({"_id": unit["id"]})
                    assert document["page_result_id"] == body["page_result_id"]
                assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 2
            again = api.post(f"/api/v1/resource-versions/{version['id']}/process")
            assert again.status_code == 200
            assert again.json()["job_id"] == job_id
            assert database.ocr_pages.count_documents({}) == 2
    finally:
        client.close()


def test_mongo_rejects_stale_projection_generation(mongo_test_database):
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000)
    database = client[mongo_test_database[1]]
    store = MongoContentStore(database)
    try:
        document = {
            "_id": "unit-1",
            "resource_version_id": "version-1",
            "text": "new",
            "is_current": True,
            "projection_generation": 2,
            "created_at": now(),
        }
        store.upsert_content_unit(document)
        with pytest.raises(DomainError, match="ownership lost"):
            store.upsert_content_unit({**document, "text": "old", "projection_generation": 1})
        store.reconcile_content_units("version-1", [], 1)
        current = database.mongo_content_units.find_one({"_id": "unit-1"})
        assert current["text"] == "new"
        assert current["is_current"] is True
    finally:
        client.close()
