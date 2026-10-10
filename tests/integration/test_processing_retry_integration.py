"""Retry contract against isolated PostgreSQL schema and MongoDB database."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from conftest import ingest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.main import create_app


@pytest.mark.postgres
def test_failed_ocr_retry_reaches_mongo_without_duplicate_work(
    pg_container, mongo_test_database, monkeypatch
):
    c = pg_container
    mongo = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000)
    database = mongo[mongo_test_database[1]]
    c.mongo_projector.documents = MongoContentStore(database)
    c.settings.admin_enabled = True
    c.ocr.max_retries = 1
    original_recognize = c.ocr.recognize
    fail_page_two = True

    def recognize(task):
        nonlocal fail_page_two
        if task["page_num"] == 2 and fail_page_two:
            fail_page_two = False
            raise RuntimeError("controlled OCR failure")
        return original_recognize(task)

    monkeypatch.setattr(c.ocr, "recognize", recognize)
    try:
        version = ingest(c, ["First page", "Second page"])
        with TestClient(create_app(c)) as api:
            response = api.post(f"/api/v1/resource-versions/{version['id']}/process")
            assert response.status_code == 202
            job_id = response.json()["job_id"]
            assert c.worker.once("first")
            assert c.worker.once("first")
            with c.uow() as repo:
                job = repo.get("ocr_jobs", job_id)
                assert job["status"] == "COMPLETED_WITH_ERRORS"
                successful = repo.find("ocr_page_tasks", {"job_id": job_id, "status": "SUCCEEDED"})
                assert len(successful) == 1
                result_id = repo.find("ocr_page_results", {"page_task_id": successful[0]["id"]})[0][
                    "id"
                ]

            retry = api.post(f"/api/v1/processing-jobs/{job_id}/retry")
            assert retry.status_code == 202
            assert retry.json()["action"] == "OCR_REQUEUED"
            assert api.post(f"/api/v1/processing-jobs/{job_id}/retry").status_code == 200
            assert c.worker.once("second")
            assert c.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
            with c.uow() as repo:
                assert repo.count("ocr_jobs", {"resource_version_id": version["id"]}) == 1
                assert repo.count("ocr_page_tasks", {"job_id": job_id}) == 2
                assert repo.count("ocr_page_results", {}) == 2
                assert repo.get("ocr_page_results", result_id) is not None
                assert repo.count("etl_stage_runs", {"stage": "OCR_RETRY"}) == 1
            assert database.ocr_pages.count_documents({}) == 2
            assert database.mongo_content_units.count_documents({"is_current": True}) == 2
            assert (
                api.post(f"/api/v1/processing-jobs/{job_id}/retry").json()["action"]
                == "ALREADY_COMPLETED"
            )
            assert database.ocr_pages.count_documents({}) == 2
    finally:
        mongo.close()


@pytest.mark.postgres
def test_postgres_lock_rejects_retry_while_version_is_owned(pg_container):
    c = pg_container
    c.settings.admin_enabled = True
    version = ingest(c)
    job = c.ocr.create_job(version["id"])
    c.ocr.max_retries = 1
    for _ in range(job["total_pages"]):
        assert c.ocr.fail(c.ocr.claim("initial"))
    entered, release = Event(), Event()

    def hold_lock():
        with c.uow.processing_lock(version["id"]) as acquired:
            assert acquired
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        owner = pool.submit(hold_lock)
        try:
            assert entered.wait(10)
            with TestClient(create_app(c)) as api:
                assert api.post(f"/api/v1/processing-jobs/{job['id']}/retry").status_code == 409
        finally:
            release.set()
        owner.result(timeout=10)
    with TestClient(create_app(c)) as api:
        assert api.post(f"/api/v1/processing-jobs/{job['id']}/retry").status_code == 202
    with c.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "OCR_RETRY"}) == 1


@pytest.mark.postgres
def test_failed_projection_retry_is_durable_and_reconciled(
    pg_container, mongo_test_database, monkeypatch
):
    c = pg_container
    mongo = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000)
    database = mongo[mongo_test_database[1]]
    c.mongo_projector.documents = MongoContentStore(database)
    c.settings.admin_enabled = True
    c.processing_finalizer.max_attempts = 1
    original = c.mongo_projector.documents.upsert_ocr_page
    writes = 0

    def fail_once(document):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise RuntimeError("controlled MongoDB outage")
        return original(document)

    monkeypatch.setattr(c.mongo_projector.documents, "upsert_ocr_page", fail_once)
    try:
        version = ingest(c, ["First page", "Second page"])
        with TestClient(create_app(c)) as api:
            response = api.post(f"/api/v1/resource-versions/{version['id']}/process")
            assert response.status_code == 202
            job_id = response.json()["job_id"]
            assert c.worker.once("first")
            assert c.worker.once("first")
            assert (
                c.orchestrator.status(version["id"])["processing_status"] == "FINALIZATION_FAILED"
            )
            assert database.ocr_pages.count_documents({}) == 1
            with c.uow() as repo:
                result_ids = {row["id"] for row in repo.find("ocr_page_results", {})}
            retry = api.post(f"/api/v1/processing-jobs/{job_id}/retry")
            assert retry.status_code == 202
            assert retry.json()["action"] == "FINALIZATION_REQUEUED"
            assert api.post(f"/api/v1/processing-jobs/{job_id}/retry").status_code == 200
            with c.uow() as repo:
                assert (
                    repo.count(
                        "etl_stage_runs", {"stage": "FINALIZATION_RETRY", "status": "PENDING"}
                    )
                    == 1
                )
            assert c.worker.once("reconciler")
            assert c.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
            with c.uow() as repo:
                assert {row["id"] for row in repo.find("ocr_page_results", {})} == result_ids
                assert (
                    repo.count(
                        "etl_stage_runs", {"stage": "FINALIZATION_RETRY", "status": "COMPLETED"}
                    )
                    == 1
                )
            assert database.ocr_pages.count_documents({}) == 2
            assert database.mongo_content_units.count_documents({"is_current": True}) == 2
    finally:
        mongo.close()
