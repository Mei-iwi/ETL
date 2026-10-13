from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256

import pymupdf
import pytest
from conftest import migrate
from pymongo import MongoClient
from sqlalchemy import inspect, text

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.application.sync import MasterSync

pytestmark = pytest.mark.postgres


def test_import_queue_migration_up_down_up(pg_container):
    engine = pg_container.engine
    assert "source_file_imports" in inspect(engine).get_table_names()
    migrate(engine, "0010", downgrade=True)
    assert "source_file_imports" not in inspect(engine).get_table_names()
    migrate(engine)
    assert "source_file_imports" in inspect(engine).get_table_names()


def setup_import(source, c, tmp_path):
    root = tmp_path / "source"
    path = root / "books" / "native.pdf"
    path.parent.mkdir(parents=True)
    with pymupdf.open() as doc:
        doc.new_page().insert_text((72, 72), "A genuine native text source PDF for integration testing.")
        doc.save(path)
    with source.engine.begin() as connection:
        connection.execute(text(f'GRANT SELECT ON "{source.schema}".resource_versions TO "{source.role}"'))
        user = connection.scalar(text("SELECT id FROM users ORDER BY id LIMIT 1"))
        connection.execute(text(
            "INSERT INTO resource_versions (id,resource_id,version_number,lifecycle_status,"
            "storage_bucket,storage_object_key,source_hash,uploaded_by,created_at,updated_at) "
            "VALUES ('source-local-1','resource-1',1,'APPROVED','books','native.pdf',:hash,:user,now(),now())"
        ), {"hash": sha256(path.read_bytes()).hexdigest(), "user": user})
    c.source = source.adapter
    c.source_file_import.source = source.adapter
    c.source_files.root = root
    c.sync = MasterSync(source.adapter, c.uow, batch_size=2)
    c.master_sync_worker.sync = c.sync
    c.sync.run(full=True)
    return path


def test_postgresql_concurrent_claim_and_mapping(source, pg_container, tmp_path):
    c = pg_container
    path = setup_import(source, c, tmp_path)
    original = path.read_bytes()
    c.source_file_import.discover_once()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: c.source_file_import.claim(), range(2)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    c.source_file_import.execute(winners[0])
    c.sync.run(full=False)
    c.source_file_import.discover_once()
    with c.uow() as repo:
        assert repo.count("source_file_imports", {}) == repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 1
    assert original == path.read_bytes()
    assert c.source_file_import.list_files(failed_only=True)["items"] == []
    with c.uow() as repo:
        job = repo.find("ocr_jobs", limit=1)[0]
        repo.update("ocr_jobs", job["id"], {"status": "FAILED", "failed_pages": job["total_pages"]})
    assert len(c.source_file_import.list_files(failed_only=True)["items"]) == 1


def test_local_source_to_real_postgresql_and_mongo(source, pg_container, mongo_test_database, tmp_path):
    c = pg_container
    uri, database_name = mongo_test_database
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    try:
        database = client[database_name]
        c.mongo_projector.documents = MongoContentStore(database)
        setup_import(source, c, tmp_path)
        # Exercise the existing Auto coordinator instead of asking a browser to schedule work.
        c.auto_sync.environment_supported = True
        c.auto_sync.source_health = source.adapter.health
        c.auto_sync.heartbeat_and_claim("master-test")
        c.auto_sync.configure(True, "resources")
        with c.uow() as repo:
            repo.update("sync_auto_config", "master-data", {"next_run_at": None})
        assert c.master_sync_worker.once("master-test")["action"] == "COMPLETED"
        assert c.source_file_worker.run("file-test", once=True)
        for _ in range(10):
            if not c.worker.once("ocr-test"):
                break
        files = c.source_file_import.list_files()
        assert files["items"][0]["etl_status"] == "CONTENT_READY"
        with c.uow() as repo:
            assert repo.count("content_units", {}) > 0
            assert repo.count("source_file_imports", {}) == repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 1
        assert database.ocr_pages.count_documents({}) == 1
        assert database.mongo_content_units.count_documents({}) > 0
        counts = (database.ocr_pages.count_documents({}), database.mongo_content_units.count_documents({}))
        c.sync.run(full=True)
        assert c.source_file_worker.run("file-test", once=True) is False
        assert counts == (database.ocr_pages.count_documents({}), database.mongo_content_units.count_documents({}))
    finally:
        client.close()
