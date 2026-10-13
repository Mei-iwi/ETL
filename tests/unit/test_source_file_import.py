from datetime import timedelta
from hashlib import sha256

import pymupdf
import pytest
from fastapi.testclient import TestClient

from data_sync_etl.adapters.storage.source_local import SourceFileError, SourceLocalFileReader
from data_sync_etl.application.source_file_import import SourceImportConflict
from data_sync_etl.domain.core import now
from data_sync_etl.main import create_app


@pytest.fixture
def importing(container, tmp_path, monkeypatch):
    container.sync.run(full=True)
    root = tmp_path / "source-files"
    path = root / "books" / "test.pdf"
    path.parent.mkdir(parents=True)
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((72, 72), "A real native text PDF for source import tests.")
        doc.save(path)
    container.settings.source_file_storage_root = root
    container.source_files.root = root
    rows = [{
        "source_version_id": "source-version-1", "resource_id": "resource-1",
        "resource_title": "Source PDF", "source_version_number": 1,
        "source_lifecycle_status": "APPROVED", "updated_at": now(),
        "source_hash": sha256(path.read_bytes()).hexdigest(),
        "storage_bucket": "books", "storage_object_key": "test.pdf",
    }]

    def page(after=None, size=20):
        ordered = sorted((r.copy() for r in rows if after is None or r["source_version_id"] > after),
                         key=lambda r: r["source_version_id"])
        return {"items": ordered[:size], "next_cursor": ordered[size - 1]["source_version_id"]
                if len(ordered) > size else None}

    monkeypatch.setattr(container.source, "version_page", page, raising=False)
    yield container, rows, path


def discover_and_import(container):
    container.source_file_import.discover_once()
    claimed = container.source_file_import.claim()
    assert claimed is not None
    container.source_file_import.execute(claimed)
    with container.uow() as repo:
        return repo.get("source_file_imports", claimed["id"])


@pytest.mark.parametrize("bucket,key", [
    ("", "test.pdf"), ("books", ""), ("books", "../test.pdf"), ("..", "test.pdf"),
    ("books", "/absolute.pdf"), ("books", "D:/test.pdf"), ("books", "\\\\server\\file.pdf"),
    ("books", "folder//file.pdf"), ("books", "./file.pdf"), ("books", "file\x00.pdf"),
    ("books/sub", "file.pdf"), ("books", "NUL.pdf"), ("books", "file.pdf:secret"),
])
def test_source_reader_rejects_paths(importing, bucket, key):
    c, _, _ = importing
    with pytest.raises(SourceFileError, match="INVALID_SOURCE_PATH"):
        c.source_files.open_stream(bucket, key)


def test_source_reader_root_separation_and_missing(importing, tmp_path):
    c, _, path = importing
    with c.source_files.open_stream("books", "test.pdf") as stream:
        assert stream.read(5) == b"%PDF-"
    with pytest.raises(SourceFileError, match="FILE_NOT_FOUND"):
        c.source_files.open_stream("books", "missing.pdf")
    for root in (None, tmp_path / "missing", c.storage.root, c.storage.root.parent):
        with pytest.raises(SourceFileError, match="BLOCKED_SOURCE_STORAGE"):
            SourceLocalFileReader(root, c.storage.root).check()
    assert path.exists()


def test_source_reader_symlink(importing, tmp_path):
    c, _, path = importing
    link = path.parent / "link.pdf"
    try:
        link.symlink_to(path)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(SourceFileError, match="INVALID_SOURCE_PATH"):
        c.source_files.open_stream("books", "link.pdf")


def test_source_reader_permission_error(importing, monkeypatch):
    c, _, _ = importing
    def denied(*args, **kwargs):
        raise PermissionError("private-source-location")
    monkeypatch.setattr("data_sync_etl.adapters.storage.source_local.os.open", denied)
    with pytest.raises(SourceFileError, match="PERMISSION_DENIED"):
        c.source_files.open_stream("books", "test.pdf")


def test_import_size_limit_before_publication(importing):
    c, _, _ = importing
    c.source_file_import.validator.max_size_bytes = 10
    row = discover_and_import(c)
    assert row["last_error_code"] == "FILE_TOO_LARGE"
    assert not list((c.storage.root / "resources").rglob("*.pdf"))


def test_import_native_pdf_runs_existing_pipeline_without_duplicates(importing):
    c, _, path = importing
    original = path.read_bytes()
    row = discover_and_import(c)
    assert row["status"] == "PROCESSING" and row["target_version_id"] != row["source_version_id"]
    for _ in range(10):
        if not c.worker.once("ocr-test"):
            break
    files = c.source_file_import.list_files()
    assert files["items"][0]["etl_status"] == "CONTENT_READY"
    assert c.documents.pages and c.documents.units  # In-memory document port, not real Mongo E2E.
    c.sync.run(full=True)
    c.source_file_import.discover_once()
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == repo.count("source_file_imports", {}) == 1
    assert path.read_bytes() == original


@pytest.mark.parametrize("fault,expected", [("hash", "SOURCE_HASH_MISMATCH"),
                                          ("empty", "INVALID_PDF"), ("malformed", "INVALID_PDF"),
                                          ("missing", "FILE_NOT_FOUND"), ("policy", "BLOCKED_SOURCE_POLICY")])
def test_permanent_fault_does_not_register_or_hot_retry(importing, fault, expected):
    c, rows, path = importing
    if fault == "hash":
        rows[0]["source_hash"] = "0" * 64
    elif fault in {"empty", "malformed"}:
        path.write_bytes(b"" if fault == "empty" else b"%PDF-not-a-document")
        rows[0]["source_hash"] = sha256(path.read_bytes()).hexdigest()
    elif fault == "missing":
        path.unlink()
    else:
        rows[0]["source_lifecycle_status"] = "REJECTED"
    row = discover_and_import(c)
    assert row["status"] == "FAILED" and row["last_error_code"] == expected
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 0


def test_multiple_versions_same_bytes_keep_identity(importing):
    c, rows, _ = importing
    first = discover_and_import(c)
    rows.append({**rows[0], "source_version_id": "source-version-2", "source_version_number": 2})
    c.sync.run(full=False)
    second = discover_and_import(c)
    assert first["target_version_id"] != second["target_version_id"]
    with c.uow() as repo:
        v1 = repo.get("resource_versions", first["target_version_id"])
        v2 = repo.get("resource_versions", second["target_version_id"])
        assert v2["parent_version_id"] == v1["id"]
        assert v2["storage_object_key"] == v1["storage_object_key"]


def test_source_mutation_is_not_overwritten(importing):
    c, rows, _ = importing
    row = discover_and_import(c)
    rows[0]["source_hash"] = "b" * 64
    c.sync.run(full=True)
    c.source_file_import.discover_once()
    with c.uow() as repo:
        changed = repo.get("source_file_imports", row["id"])
        assert changed["last_error_code"] == "SOURCE_VERSION_MUTATED"
        assert changed["target_version_id"] == row["target_version_id"]
    with pytest.raises(SourceImportConflict):
        c.source_file_import.retry(row["id"])


def test_manual_version_collision_is_blocked(importing):
    c, _, _ = importing
    with c.uow() as repo:
        repo.insert("resource_versions", {"resource_id": "resource-1", "version_number": 1,
                    "lifecycle_status": "INGESTED", "storage_bucket": "manual", "storage_object_key": "manual.pdf",
                    "source_hash": "0" * 64})
    row = discover_and_import(c)
    assert row["last_error_code"] == "BLOCKED_SOURCE_MAPPING"
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 1


def test_missing_master_and_transient_budget(importing):
    c, rows, _ = importing
    rows[0]["resource_id"] = "missing-resource"
    c.settings.source_file_import_max_attempts = 2
    row = discover_and_import(c)
    assert row["status"] == "RETRY_WAIT" and row["next_retry_at"] > now()
    with c.uow() as repo:
        repo.update("source_file_imports", row["id"], {"next_retry_at": now() - timedelta(seconds=1)})
    claimed = c.source_file_import.claim()
    c.source_file_import.execute(claimed)
    with c.uow() as repo:
        assert repo.get("source_file_imports", row["id"])["status"] == "RETRY_EXHAUSTED"


def test_expired_lease_fences_old_worker(importing):
    c, _, _ = importing
    c.source_file_import.discover_once()
    old = c.source_file_import.claim()
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        repo.update("source_file_imports", old["id"], {"lease_expires_at": now() - timedelta(seconds=1)})
    new = c.source_file_import.claim()
    c.source_file_import.execute(old)
    c.source_file_import.execute(new)
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 1


def test_crash_after_registration_resumes_without_recopy(importing, monkeypatch):
    c, _, _ = importing
    process = c.process_version.run
    monkeypatch.setattr(c.process_version, "run", lambda _: (_ for _ in ()).throw(RuntimeError("private")))
    row = discover_and_import(c)
    assert row["target_version_id"] and row["status"] == "RETRY_WAIT"
    monkeypatch.setattr(c.process_version, "run", process)
    monkeypatch.setattr(c.source_files, "open_stream", lambda *_: (_ for _ in ()).throw(AssertionError("No recopy")))
    with c.uow() as repo:
        repo.update("source_file_imports", row["id"], {"next_retry_at": now() - timedelta(seconds=1)})
    c.source_file_import.execute(c.source_file_import.claim())
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 1


def test_retry_api_queues_only_failed_and_hides_storage(importing):
    c, rows, _ = importing
    rows[0]["storage_object_key"] = "missing.pdf"
    row = discover_and_import(c)
    c.settings.admin_enabled = True
    app = create_app(c)
    with TestClient(app, client=("127.0.0.1", 5555)) as client:
        listed = client.get("/api/v1/master-data/sync/files").json()
        assert listed["items"][0]["retry_eligible"]
        assert all(key not in listed["items"][0] for key in ["source_hash", "storage_bucket", "storage_object_key"])
        response = client.post("/api/v1/master-data/sync/files/retry-failed")
        assert response.status_code == 202 and response.json()["accepted_count"] == 1
        assert client.post(f'/api/v1/master-data/sync/files/{row["id"]}/retry').status_code == 409
        assert client.post("/api/v1/master-data/sync/files/unknown/retry").status_code == 404
    with TestClient(app, client=("203.0.113.1", 5555)) as client:
        assert client.post("/api/v1/master-data/sync/files/retry-failed").status_code == 403
        assert client.get("/api/v1/master-data/sync/files").status_code == 403


def test_crash_after_publication_reuses_bytes(importing, monkeypatch):
    c, _, _ = importing
    service = c.source_file_import
    master = service._master
    checks = 0
    def interrupted(repo, resource_id):
        nonlocal checks
        checks += 1
        if checks == 2:
            raise RuntimeError("Simulated commit interruption")
        return master(repo, resource_id)
    monkeypatch.setattr(service, "_master", interrupted)
    row = discover_and_import(c)
    assert row["target_version_id"] is None and row["status"] == "RETRY_WAIT"
    assert len(list((c.storage.root / "resources").rglob("*.pdf"))) == 1
    monkeypatch.setattr(service, "_master", master)
    with c.uow() as repo:
        repo.update("source_file_imports", row["id"], {"next_retry_at": now() - timedelta(seconds=1)})
    service.execute(service.claim())
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 1
    assert len(list((c.storage.root / "resources").rglob("*.pdf"))) == 1


def test_ocr_manual_retry_keeps_file_and_job(importing, monkeypatch):
    c, _, _ = importing
    row = discover_and_import(c)
    recognize = c.ocr.recognize
    def fail(*args):
        raise RuntimeError("Transient OCR error")
    monkeypatch.setattr(c.ocr, "recognize", fail)
    for _ in range(10):
        if not c.worker.once("test-ocr"):
            break
    before = c.source_file_import.list_files()["items"][0]
    assert before["etl_status"] == "OCR_FAILED" and before["retry_eligible"]
    c.source_files.root = None  # Processing retry must work even when source storage is offline.
    assert c.source_file_import.list_files()["items"][0]["retry_eligible"]
    c.source_file_import.retry(row["id"])
    monkeypatch.setattr(c.source_files, "open_stream", lambda *_: (_ for _ in ()).throw(AssertionError("No recopy")))
    c.source_file_import.execute(c.source_file_import.claim())
    monkeypatch.setattr(c.ocr, "recognize", recognize)
    for _ in range(10):
        if not c.worker.once("test-ocr"):
            break
    after = c.source_file_import.list_files()["items"][0]
    assert after["etl_status"] == "CONTENT_READY" and not after["retry_eligible"]
    assert after["processing_job_id"] == before["processing_job_id"]
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 1


def test_projection_retry_does_not_recopy_or_rerun_ocr(importing, monkeypatch):
    c, _, _ = importing
    c.settings.finalization_max_attempts = c.processing_finalizer.max_attempts = 1
    row = discover_and_import(c)
    write = c.documents.upsert_content_unit
    def fail(*args):
        raise RuntimeError("Mongo temporarily unavailable")
    monkeypatch.setattr(c.documents, "upsert_content_unit", fail)
    for _ in range(10):
        if not c.worker.once("test-ocr"):
            break
    before = c.source_file_import.list_files()["items"][0]
    assert before["etl_status"] == "FINALIZATION_FAILED" and before["retry_eligible"]
    c.source_file_import.retry(row["id"])
    monkeypatch.setattr(c.source_files, "open_stream", lambda *_: (_ for _ in ()).throw(AssertionError("No recopy")))
    c.source_file_import.execute(c.source_file_import.claim())
    monkeypatch.setattr(c.documents, "upsert_content_unit", write)
    c.worker.once("test-ocr")
    after = c.source_file_import.list_files()["items"][0]
    assert after["etl_status"] == "CONTENT_READY"
    assert after["processing_job_id"] == before["processing_job_id"]
    with c.uow() as repo:
        assert repo.count("ocr_page_tasks", {}) == 1
