from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import make_pdf, migrate
from fastapi.testclient import TestClient
from pymongo import MongoClient
from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.exc import DBAPIError

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.main import create_app

from .test_source import execute, use_source

pytestmark = pytest.mark.postgres


def test_source_sync_worker_mongo_and_runtime_preservation(
    source, pg_container, mongo_test_database
):
    c = pg_container
    use_source(c, source)
    c.sync.run(full=True)
    client = MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000)
    database = client[mongo_test_database[1]]
    c.mongo_projector.documents = MongoContentStore(database)
    c.settings.admin_enabled = True
    try:
        assert database.list_collection_names() == []
        make_pdf(c, ["Source page one", "Source page two"])
        version = c.ingestion.run("resource-1", "input", "sample.pdf")["version"]
        with TestClient(create_app(c)) as api:
            response = api.post(f"/api/v1/resource-versions/{version['id']}/process")
            assert response.status_code == 202
            for _ in range(8):
                if not c.worker.once("source-test-worker"):
                    break
            assert c.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
        assert database.ocr_pages.count_documents({}) == 2
        assert database.mongo_content_units.count_documents({"is_current": True}) == 2
        with c.uow() as repo:
            before = {
                table: repo.find(table, {})
                for table in (
                    "resource_versions",
                    "ocr_jobs",
                    "ocr_page_tasks",
                    "ocr_page_results",
                    "content_units",
                )
            }
            for task in before["ocr_page_tasks"]:
                page = database.ocr_pages.find_one({"_id": f"{task['job_id']}:{task['page_num']}"})
                result = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
                assert page["raw_text"] == result["raw_text"]
                assert page["resource_version_id"] == version["id"]
        execute(source, "UPDATE providers SET name='New source provider' WHERE id='provider-1'")
        c.sync.run(full=True)
        with c.uow() as repo:
            assert all(repo.find(table, {}) == rows for table, rows in before.items())
        with source.engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM resource_versions")) == 0
        assert set(database.list_collection_names()) == {"ocr_pages", "mongo_content_units"}
    finally:
        client.close()


def test_sync_batch_rollback_keeps_checkpoint(source, pg_container, monkeypatch):
    c = pg_container
    use_source(c, source)
    c.sync.run(full=True)
    with c.uow() as repo:
        before = repo.get("sync_checkpoints", "resources")
        title = repo.get("master_learning_resources", "resource-1")["title"]
    execute(source, "UPDATE learning_resources SET title='rollback test' WHERE id='resource-1'")
    original = SQLAlchemyRepository.sync_upsert

    def fail_after_write(self, table, values):
        result = original(self, table, values)
        if table == "master_learning_resources" and values["id"] == "resource-1":
            raise RuntimeError("controlled failure after write")
        return result

    with monkeypatch.context() as patch:
        patch.setattr(SQLAlchemyRepository, "sync_upsert", fail_after_write)
        with pytest.raises(DomainError):
            c.sync.run("resources")
    with c.uow() as repo:
        assert repo.get("sync_checkpoints", "resources") == before
        assert repo.get("master_learning_resources", "resource-1")["title"] == title
    c.sync.run("resources")
    with c.uow() as repo:
        assert repo.get("master_learning_resources", "resource-1")["title"] == "rollback test"


def test_master_content_parent_and_local_timestamp(pg_container):
    c = pg_container
    c.sync.run(full=True)
    make_pdf(c)
    first = c.ingestion.run("resource-1", "input", "sample.pdf")["version"]
    other = c.ingestion.run("resource-2", "input", "sample.pdf")["version"]
    with c.uow() as repo:
        for number, version in [(1, first), (2, first), (3, other)]:
            repo.insert(
                "content_units",
                dict(
                    id=f"unit-{number}",
                    resource_version_id=version["id"],
                    parent_unit_id=None,
                    unit_type="SECTION",
                    title="before",
                    sequence_no=number,
                    review_status="PENDING",
                    retrieval_eligible=False,
                ),
            )
        before = repo.get("content_units", "unit-1")
        master_before = repo.get("master_learning_resources", "resource-1")
    with c.engine.begin() as connection:
        connection.execute(text("UPDATE content_units SET title='after' WHERE id='unit-1'"))
    with c.uow() as repo:
        after = repo.get("content_units", "unit-1")
        assert after["updated_at"] > before["updated_at"]
        assert after["created_at"] == before["created_at"]
        assert repo.get("master_learning_resources", "resource-1") == master_before
    for unit, parent in [("unit-1", "unit-1"), ("unit-1", "unit-3")]:
        with pytest.raises(DBAPIError), c.engine.begin() as connection:
            connection.execute(
                text("UPDATE content_units SET parent_unit_id=:parent WHERE id=:id"),
                dict(id=unit, parent=parent),
            )
    barrier = Barrier(2)

    def assign(values):
        try:
            with c.engine.begin() as connection:
                barrier.wait(timeout=5)
                connection.execute(
                    text("UPDATE content_units SET parent_unit_id=:parent WHERE id=:id"), values
                )
            return True
        except DBAPIError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                assign, [dict(id="unit-1", parent="unit-2"), dict(id="unit-2", parent="unit-1")]
            )
        )
    assert sorted(outcomes) == [False, True]


def test_master_migration_preserves_rows_and_guards_null_downgrade(pg_container):
    c = pg_container
    migrate(c.engine, "0004", downgrade=True)
    # Seed through reflected HISTORICAL tables, not an ORM requiring current columns.
    tables = {name: Table(name, MetaData(), autoload_with=c.engine) for name in
              ('master_learning_resources', 'master_users', 'sync_checkpoints')}
    with c.engine.begin() as connection:
        for stream, name in [('resources', 'master_learning_resources'), ('users', 'master_users')]:
            for batch in c.source.batches(stream, None, 2):
                for aggregate in batch:
                    connection.execute(tables[name].insert().values(aggregate.values))
        connection.execute(tables['sync_checkpoints'].insert().values(
            stream_name='resources', last_id='resource-3', last_updated_at=now(), updated_at=now()))
        before = {name: list(connection.execute(select(table).order_by(*table.primary_key.columns)).mappings())
                  for name, table in tables.items()}
    migrate(c.engine)
    migrate(c.engine)
    with c.engine.connect() as connection:
        assert all(list(connection.execute(select(tables[name]).order_by(*tables[name].primary_key.columns)).mappings()) == rows
                   for name, rows in before.items())
    with c.uow() as repo:
        repo.update("master_learning_resources", "resource-1", {"title": None})
    with pytest.raises(RuntimeError, match="NULL rows"):
        migrate(c.engine, "0004", downgrade=True)
    with c.engine.connect() as connection:
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from conftest import ROOT
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == ScriptDirectory.from_config(Config(str(ROOT / 'alembic.ini'))).get_current_head()
