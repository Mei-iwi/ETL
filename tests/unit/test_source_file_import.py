from hashlib import sha256

import pymupdf
import pytest

from data_sync_etl.adapters.storage.source_local import SourceFileError, SourceLocalFileReader
from data_sync_etl.application.source_file_import import (
    METADATA_ONLY_BUCKET,
    METADATA_ONLY_ERROR,
    SourceImportConflict,
)
from data_sync_etl.domain.core import now
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


@pytest.fixture
def importing(container, tmp_path, monkeypatch):
    container.sync.run(full=True)
    container.settings.source_reference_enabled = True
    container.settings.source_temporary_files_enabled = True
    container.settings.source_temp_root = tmp_path / 'private-source-temp'
    with container.uow() as repo:
        for resource in repo.find('master_learning_resources'):
            repo.update('master_learning_resources', resource['id'], {'source_origin': 'POSTGRESQL'})
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
        "resource_title": "Source PDF", "source_version_number": 1, "parent_version_id": None,
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
    monkeypatch.setattr(container.source, "get_version", lambda id: next(
        (dict(r) for r in rows if r["source_version_id"] == id), None), raising=False)
    yield container, rows, path


def discover_and_import(container):
    container.source_file_import.discover_once()
    with container.uow() as repo:
        return repo.find("source_file_imports", order=("source_version_id",), limit=1)[0]


def test_integrity_filter_bounds_work_and_resumes_even_with_no_matches():
    from contextlib import contextmanager
    from types import SimpleNamespace

    from data_sync_etl.application.source_file_import import SourceFileImportService
    reads = []
    def page(after, size):
        start = int(after) + 1 if after is not None else 0
        reads.append(start)
        return [{'source_version_id': f'{number:04d}'} for number in range(start, min(start + size, 1005))]
    repo = SimpleNamespace(source_import_page=page, find=lambda *args, **kwargs: [True])
    @contextmanager
    def uow():
        yield repo
    service = object.__new__(SourceFileImportService)
    service.uow, service.source = uow, SimpleNamespace()
    service.availability = lambda: {}
    first = service.list_files(size=7, integrity_only=True,
        evidence_reader=lambda *args: {'recent_sha256_evidence': True})
    assert first['items'] == [] and first['next_cursor'] == '0999'
    assert len(reads) == 10 and first['total'] is None
    second = service.list_files(after=first['next_cursor'], size=7, integrity_only=True,
        evidence_reader=lambda *args: {'recent_sha256_evidence': True})
    assert second['items'] == [] and second['next_cursor'] is None
    assert reads[-1] == 1000


def test_file_totals_are_requested_separately_from_polling(importing):
    c, _, _ = importing
    c.source_file_import.discover_once()
    first = c.source_file_import.list_files(size=7, include_total=True)
    assert first['total'] == 1 and first['total_pages'] == 1
    polled = c.source_file_import.list_files(size=7)
    assert polled['total'] is None and polled['total_pages'] is None
    assert len(polled['items']) == 1


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
    c, _, path = importing
    c.source_file_import.validator.max_size_bytes = 10
    row = discover_and_import(c)
    assert row["status"] == "METADATA_ONLY"
    assert row["last_error_code"] == METADATA_ONLY_ERROR
    assert row["storage_bucket"] == METADATA_ONLY_BUCKET
    assert path.exists()
    assert not list((c.storage.root / "resources").rglob("*.pdf"))


def test_import_native_pdf_runs_existing_pipeline_without_duplicates(importing):
    c, _, path = importing
    original = path.read_bytes()
    row = discover_and_import(c)
    assert row["status"] == "METADATA_ONLY" and row["target_version_id"] != row["source_version_id"]
    assert c.source_file_import.claim() is None
    files = c.source_file_import.list_files()
    assert files["items"][0]["retry_eligible"] is False
    assert not c.documents.pages and not c.documents.units
    c.sync.run(full=True)
    c.source_file_import.discover_once()
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == repo.count("source_file_imports", {}) == 1
        assert repo.count("ocr_jobs", {}) == 0
    assert path.read_bytes() == original


@pytest.mark.parametrize("fault", ["hash", "empty", "malformed", "missing", "policy"])
def test_source_metadata_discovery_never_reads_file_bytes(importing, monkeypatch, fault):
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
    def forbidden(*args, **kwargs):
        pytest.fail('External SOURCE bytes must never be read')
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    row = discover_and_import(c)
    assert row["status"] == "METADATA_ONLY"
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.count("ocr_jobs", {}) == 0
        assert all(version['lifecycle_status'] == 'METADATA_ONLY'
                   for version in repo.find('resource_versions'))


def test_multiple_versions_same_bytes_keep_identity(importing):
    c, rows, _ = importing
    first = discover_and_import(c)
    rows.append({**rows[0], "source_version_id": "source-version-2", "source_version_number": 2,
                             "parent_version_id": rows[0]["source_version_id"]})
    with c.uow() as repo:
        c.source_file_import._discover(repo, rows[-1])
        second, = repo.find('source_file_imports', {'source_version_id': 'source-version-2'})
    assert first["target_version_id"] != second["target_version_id"]
    with c.uow() as repo:
        v1 = repo.get("resource_versions", first["target_version_id"])
        v2 = repo.get("resource_versions", second["target_version_id"])
        assert v2["parent_version_id"] == v1["id"]
        assert v1['storage_bucket'] == v2['storage_bucket'] == METADATA_ONLY_BUCKET
        assert v1["storage_object_key"] != v2["storage_object_key"]


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
    with pytest.raises(SourceFileError, match='BLOCKED_SOURCE_MAPPING'):
        discover_and_import(c)
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 1


def test_missing_master_and_transient_budget(importing):
    c, rows, _ = importing
    rows[0]["resource_id"] = "missing-resource"
    row = discover_and_import(c)
    assert row['target_version_id'] is None
    assert row['status'] == 'METADATA_ONLY'
    assert c.source_file_import.claim() is None


def test_expired_lease_fences_old_worker(importing):
    c, _, _ = importing
    c.source_file_import.discover_once()
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 1
        assert repo.count("ocr_jobs", {}) == 0


def test_crash_after_registration_resumes_without_recopy(importing, monkeypatch):
    c, _, _ = importing
    row = discover_and_import(c)
    assert row["target_version_id"] and row["status"] == "METADATA_ONLY"
    monkeypatch.setattr(c.storage, 'save_stream', lambda *_a, **_k: pytest.fail('No external copy'))
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 1
        assert repo.count("ocr_jobs", {}) == 0


def test_retry_api_queues_only_failed_and_hides_storage(importing):
    c, rows, _ = importing
    rows[0]["storage_object_key"] = "missing.pdf"
    row = discover_and_import(c)
    c.settings.admin_enabled = True
    app = create_app(c)
    with TestClient(app, client=("127.0.0.1", 5555)) as client:
        listed = client.get("/api/v1/master-data/sync/files").json()
        assert not listed["items"][0]["retry_eligible"]
        assert all(key not in listed["items"][0] for key in ["source_hash", "storage_bucket", "storage_object_key"])
        response = client.post("/api/v1/master-data/sync/files/retry-failed")
        assert response.status_code == 202 and response.json()["accepted_count"] == 0
        assert client.post(f'/api/v1/master-data/sync/files/{row["id"]}/retry').status_code == 409
        assert client.post("/api/v1/master-data/sync/files/unknown/retry").status_code == 404
    with TestClient(app, client=("203.0.113.1", 5555)) as client:
        assert client.post("/api/v1/master-data/sync/files/retry-failed").status_code == 403
        assert client.get("/api/v1/master-data/sync/files").status_code == 403


def test_error_groups_filter_before_cursor_pagination(importing):
    c, rows, _ = importing
    rows[0]["storage_object_key"] = "missing.pdf"
    initial = discover_and_import(c)
    with c.uow() as repo:
        repo.update("source_file_imports", initial["id"], {
            "status": "FAILED", "last_error_code": "SOURCE_VERSION_MUTATED",
        })
        template = {key: value for key, value in initial.items() if key != "id"}
        for index in range(110):
            repo.insert("source_file_imports", {
                **template, "source_version_id": f"filter-{index:03d}",
                "target_version_id": None,
                "status": "FAILED",
                "last_error_code": "SOURCE_VERSION_MUTATED" if index < 102 else "FILE_NOT_FOUND",
            })
    c.settings.admin_enabled = True
    with TestClient(create_app(c), client=("127.0.0.1", 5555)) as client:
        first = client.get("/api/v1/master-data/sync/files?size=7&status=failed-retryable")
        assert first.status_code == 200
        page = first.json()
        assert page["items"] == []
        blocked = client.get("/api/v1/master-data/sync/files?size=7&status=failed-blocked").json()
        assert len(blocked["items"]) == 7
        assert all(not item["retry_eligible"] for item in blocked["items"])


def test_crash_before_mapping_revalidates_without_durable_bytes(importing, monkeypatch):
    c, _, _ = importing
    service = c.source_file_import
    row = discover_and_import(c)
    assert row["target_version_id"] and row["status"] == "METADATA_ONLY"
    assert not list((c.storage.root / "resources").rglob("*.pdf"))
    monkeypatch.setattr(c.source_files, 'open_stream', lambda *_a, **_k: pytest.fail('No SOURCE read'))
    assert service.claim() is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 1
        assert repo.count("ocr_jobs", {}) == 0
    assert not list((c.storage.root / "resources").rglob("*.pdf"))


def test_ocr_manual_retry_keeps_file_and_job(importing, monkeypatch):
    c, _, _ = importing
    row = discover_and_import(c)
    before = c.source_file_import.list_files()["items"][0]
    assert before['import_status'] == 'METADATA_ONLY'
    assert before['processing_job_id'] is None and not before['retry_eligible']
    with pytest.raises(SourceImportConflict, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        c.source_file_import.retry(row['id'])
    with pytest.raises(Exception, match='CONTENT_NOT_AVAILABLE'):
        c.ocr.create_job(row['target_version_id'])
    after = c.source_file_import.list_files()["items"][0]
    assert after['import_status'] == 'METADATA_ONLY' and not after['retry_eligible']
    assert after['processing_job_id'] is None
    with c.uow() as repo:
        assert repo.count("resource_versions", {}) == 1
        assert repo.count("ocr_jobs", {}) == 0


def test_projection_retry_does_not_recopy_or_rerun_ocr(importing, monkeypatch):
    c, _, _ = importing
    row = discover_and_import(c)
    before = c.source_file_import.list_files()["items"][0]
    assert before['import_status'] == 'METADATA_ONLY'
    assert before['processing_job_id'] is None and not before['retry_eligible']
    with pytest.raises(SourceImportConflict, match='EXTERNAL_SOURCE_CONTENT_DISABLED'):
        c.source_file_import.retry(row['id'])
    after = c.source_file_import.list_files()["items"][0]
    assert after['import_status'] == 'METADATA_ONLY'
    with c.uow() as repo:
        assert repo.count("ocr_page_tasks", {}) == 0


def test_failed_list_seven_row_pagination_excludes_pending(importing):
    c, rows, _ = importing
    with c.uow() as repo:
        for number in range(10):
            source = {**rows[0], "source_version_id": f"failed-version-{number:02d}",
                      "source_version_number": number + 1}
            c.source_file_import._discover(repo, source)
        jobs = repo.source_import_page(size=20)
        for row in jobs[:9]:
            repo.update("source_file_imports", row["id"], {
                "status": "FAILED", "last_error_code": "FILE_NOT_FOUND",
            })
    c.settings.admin_enabled = True
    with TestClient(create_app(c), client=("127.0.0.1", 5555)) as client:
        first = client.get("/api/v1/master-data/sync/files?size=7&status=failed").json()
        assert len(first["items"]) == 7 and first["next_cursor"]
        second = client.get("/api/v1/master-data/sync/files", params={
            "size": 7, "status": "failed", "after": first["next_cursor"],
        }).json()
        assert len(second["items"]) == 2 and second["next_cursor"] is None
        assert {row["import_status"] for row in first["items"] + second["items"]} == {"FAILED"}
        assert client.get("/api/v1/master-data/sync/files?status=invalid").status_code == 422
