from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from conftest import ingest, migrate
from sqlalchemy import inspect, text

from data_sync_etl.domain.core import now
from data_sync_etl.ports.processing import OCRResult

pytestmark = pytest.mark.postgres


def test_finalization_database_lock_and_recovery(pg_container, monkeypatch):
    from threading import Event

    from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
    from data_sync_etl.application.processing_finalizer import ProcessingFinalizer
    from data_sync_etl.db.session import sessions

    c = pg_container
    version = ingest(c)
    job = c.ocr.create_job(version["id"])
    for _ in range(3):
        c.ocr.succeed(c.ocr.claim("w"), OCRResult("Tiếng Việt\nTest"))
    entered, release = Event(), Event()
    original = c.mongo_projector.project

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(c.mongo_projector, "project", pause)
    second = ProcessingFinalizer(
        SQLAlchemyUnitOfWork(sessions(c.engine)), c.postprocess, c.mongo_projector
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(c.processing_finalizer.finalize, job["id"])
        try:
            assert entered.wait(10)
            assert (
                pool.submit(second.finalize, job["id"]).result(timeout=5)["status"] == "IN_PROGRESS"
            )
        finally:
            release.set()
        assert running.result(timeout=10)["status"] == "CONTENT_READY"
    assert second.finalize(job["id"])["status"] == "ALREADY_COMPLETED"
    with c.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "MONGO_PROJECTION"}) == 1


def test_postgres_manual_postprocess_respects_finalizer_lock(pg_container, monkeypatch):
    from threading import Event

    from data_sync_etl.domain.core import DomainError

    c = pg_container
    version = ingest(c)
    job = c.ocr.create_job(version["id"])
    for _ in range(3):
        c.ocr.succeed(c.ocr.claim("w"), OCRResult("text"))
    entered, release = Event(), Event()
    original = c.mongo_projector.project

    def pause(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(c.mongo_projector, "project", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(c.processing_finalizer.finalize, job["id"])
        try:
            assert entered.wait(10)
            with pytest.raises(DomainError, match="already running"):
                c.postprocess.run(version["id"])
        finally:
            release.set()
        assert running.result(timeout=10)["status"] == "CONTENT_READY"
    with c.uow() as repo:
        assert repo.count("etl_stage_runs", {"stage": "POSTPROCESS"}) == 1


def test_postgres_abandoned_projection_recovers(pg_container):
    c = pg_container
    version = ingest(c)
    job = c.ocr.create_job(version["id"])
    for _ in range(3):
        c.ocr.succeed(c.ocr.claim("w"), OCRResult("text"))
    with c.uow.processing_lock(version["id"]) as acquired:
        assert acquired
        with c.uow() as repo:
            old = repo.insert(
                "etl_stage_runs",
                {
                    "resource_version_id": version["id"],
                    "stage": "MONGO_PROJECTION",
                    "status": "RUNNING",
                    "started_at": now(),
                    "created_at": now(),
                },
            )
    # Owner transaction ended without finalizing: lock is released by PostgreSQL.
    assert c.worker.once("recovery")
    with c.uow() as repo:
        assert repo.get("etl_stage_runs", old["id"])["status"] == "FAILED"
        assert repo.get("ocr_jobs", job["id"])["completed_pages"] == 3


def test_postgres_migrations(pg_container):
    from alembic.config import Config
    from conftest import ROOT

    from alembic import command

    migrate(pg_container.engine, "base", downgrade=True)
    migrate(pg_container.engine)
    assert len(inspect(pg_container.engine).get_table_names()) == 16
    with pg_container.engine.connect() as connection:
        assert connection.scalar(text("select current_setting('server_version_num')"))
        assert len(inspect(connection).get_foreign_keys("content_unit_texts")) == 2
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.check(config)


def test_concurrent_ingestion(pg_container):
    c = pg_container
    version = ingest(c)
    from conftest import make_pdf

    make_pdf(c, ["Concurrent version two"])
    barrier = Barrier(2)

    def run():
        barrier.wait()
        return c.ingestion.run("resource-1", "input", "sample.pdf")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run(), range(2)))
    assert sorted(result["action"] for result in results) == ["CREATED", "NO_CHANGE"]
    assert all(result["version"]["version_number"] == 2 for result in results)
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 2
        assert repo.get("resource_versions", version["id"])


def test_skip_locked_claim_and_atomic_counters(pg_container):
    c = pg_container
    version = ingest(c)
    job = c.ocr.create_job(version["id"])
    barrier = Barrier(2)

    def claim(worker):
        barrier.wait()
        return c.ocr.claim(worker)

    with ThreadPoolExecutor(max_workers=2) as pool:
        tasks = list(pool.map(claim, ["one", "two"]))
    assert tasks[0]["id"] != tasks[1]["id"]
    # A locked pending row must be skipped, not block the next claimant.
    with c.uow() as repo:
        pending = repo.find("ocr_page_tasks", {"status": "PENDING"}, limit=1, lock=True)
        with ThreadPoolExecutor(max_workers=1) as pool:
            assert pool.submit(c.ocr.claim, "three").result(timeout=5) is None
        assert pending
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(pool.map(lambda t: c.ocr.succeed(t, OCRResult("text")), tasks))
    task = c.ocr.claim("three")
    c.ocr.succeed(task, OCRResult("text"))
    with c.uow() as repo:
        row = repo.get("ocr_jobs", job["id"])
        assert row["completed_pages"] == 3 and row["status"] == "COMPLETED"


def test_concurrent_job_creation(pg_container):
    c = pg_container
    version = ingest(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: c.ocr.create_job(version["id"]), range(2)))
    assert jobs[0]["id"] == jobs[1]["id"]
    with c.uow() as repo:
        assert repo.count("ocr_jobs", {}) == 1


def test_postgres_recovery_fencing(pg_container):
    c = pg_container
    version = ingest(c, ["One page"])
    c.ocr.create_job(version["id"])
    task = c.ocr.claim("crashed")
    with c.uow() as repo:
        repo.update("ocr_page_tasks", task["id"], {"heartbeat_at": now() - timedelta(hours=1)})
    c.ocr.recover_stale()
    replacement = c.ocr.claim("replacement")
    assert not c.ocr.succeed(task, OCRResult("obsolete"))
    assert c.ocr.succeed(replacement, OCRResult("valid"))
    assert c.postprocess.run(version["id"])["content_unit_count"] == 1


def test_postgres_sync_rollback_and_checkpoint(pg_container):
    import json

    from data_sync_etl.domain.core import DomainError

    c = pg_container
    c.sync.run(full=True)
    with c.uow() as repo:
        before = repo.get("sync_checkpoints", "resources")
    resource_path = c.source.root / "resources.json"
    resources = json.loads(resource_path.read_text())
    resources[0].update(title="Changed but rolled back", updated_at="2026-04-01T00:00:00Z")
    resource_path.write_text(json.dumps(resources))
    relation_path = c.source.root / "resource_subjects.json"
    relations = json.loads(relation_path.read_text())
    relations[0]["subject_id"] = "missing"
    relation_path.write_text(json.dumps(relations))
    with pytest.raises(DomainError):
        c.sync.run("resources")
    with c.uow() as repo:
        assert repo.get("sync_checkpoints", "resources") == before
        assert (
            repo.get("master_learning_resources", "resource-1")["title"]
            != "Changed but rolled back"
        )
    relations[0]["subject_id"] = "subject-1"
    relation_path.write_text(json.dumps(relations))
    assert c.sync.run("resources")["resources"]["updated"] == 1


def test_concurrent_postprocess_and_rollback(pg_container):
    from conftest import completed

    from data_sync_etl.domain.core import DomainError

    c = pg_container
    version, _ = completed(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(c.postprocess.run, version["id"]) for _ in range(2)]
        outcomes = []
        for future in futures:
            try:
                outcomes.append(future.result())
            except DomainError as error:
                assert "already running" in str(error)
    assert outcomes
    assert all(result == c.postprocess.run(version["id"]) for result in outcomes)
    with c.uow() as repo:
        before = repo.find("content_unit_texts", {}, order=("content_unit_id",))
        assert len(before) == 3
    calls = 0

    def broken(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("parser failure")
        return "changed"

    c.postprocess.normalizer = broken
    with pytest.raises(DomainError):
        c.postprocess.run(version["id"])
    with c.uow() as repo:
        assert before == repo.find("content_unit_texts", {}, order=("content_unit_id",))


def test_concurrent_stale_recovery_counter(pg_container):
    c = pg_container
    version = ingest(c, ["one"])
    job = c.ocr.create_job(version["id"])
    c.ocr.max_retries = 1
    task = c.ocr.claim("crashed")
    with c.uow() as repo:
        repo.update("ocr_page_tasks", task["id"], {"heartbeat_at": now() - timedelta(hours=1)})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: c.ocr.recover_stale(), range(2)))
    assert sum(r["recovered"] for r in results) == 1
    with c.uow() as repo:
        assert repo.get("ocr_jobs", job["id"])["failed_pages"] == 1


def test_concurrent_master_sync(pg_container):
    c = pg_container
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: c.sync.run(full=True), range(2)))
    assert sum(r["resources"]["inserted"] for r in results) == 3
    with c.uow() as repo:
        assert repo.count("master_learning_resources", {}) == 3
        assert repo.count("resource_subjects", {}) == 3
