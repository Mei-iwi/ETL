"""Phase 03 policy regression on isolated PostgreSQL SOURCE and Master."""

import json
from datetime import timedelta
from hashlib import sha256
from unittest.mock import Mock

import pytest
from pymongo import MongoClient
from sqlalchemy import text

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.schema import contracts, initialize
from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
from data_sync_etl.adapters.storage.source_google_drive import (
    API,
    FOLDER,
    SourceGoogleDriveFileReader,
)
from data_sync_etl.domain.core import now
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from tests.unit.test_multiformat import FORMATS, ImageEngineDouble, sample

from .test_file_import_flow import setup_import

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("revocation", ["source_rejected", "source_inactive", "master_inactive"])
def test_copied_version_retry_cannot_bypass_updated_policy(
    source, pg_container, tmp_path, monkeypatch, revocation
):
    c = pg_container
    path = setup_import(source, c, tmp_path)
    payload = path.read_bytes()
    process = Mock(side_effect=RuntimeError("isolated queue outage"))
    monkeypatch.setattr(c.source_file_import.process, "run", process)
    assert c.source_file_worker.once("policy-retry")
    with c.uow() as repo:
        imported = repo.find("source_file_imports")[0]
        version = repo.get("resource_versions", imported["target_version_id"])
        assert version and imported["processing_job_id"] is None
        repo.update(
            "source_file_imports", imported["id"], {"next_retry_at": now() - timedelta(seconds=1)}
        )
    if revocation == "master_inactive":
        with c.uow() as repo:
            repo.update("master_learning_resources", imported["resource_id"], {"is_active": False})
    else:
        statement = (
            "UPDATE resource_versions SET lifecycle_status='REJECTED', updated_at=now() "
            "WHERE id='source-local-1'"
            if revocation == "source_rejected"
            else "UPDATE learning_resources SET is_active=false, updated_at=now() "
            "WHERE id='resource-1'"
        )
        with source.engine.begin() as connection:
            connection.execute(text(statement))
        # Refresh from the real readonly SOURCE adapter, not fabricated queue policy.
        c.sync.run(full=False)
        assert c.source_file_import.discover_once()
    claimed = c.source_file_import.claim("policy-retry")
    assert claimed is not None
    c.source_file_import.execute(claimed)
    assert process.call_count == 1
    with c.uow() as repo:
        current = repo.get("source_file_imports", imported["id"])
        assert current["target_version_id"] == version["id"]
        assert current["last_error_code"] == (
            "BLOCKED_SOURCE_POLICY" if revocation == "source_rejected" else "WAITING_FOR_MASTER"
        )
        assert repo.count("ocr_jobs", {}) == 0
        assert repo.count("resource_versions", {}) == 1
    listed = c.source_file_import.list_files()["items"][0]
    assert not listed["retry_eligible"]
    target = c.storage.get_local_path(version["storage_bucket"], version["storage_object_key"])
    assert sha256(target.read_bytes()).hexdigest() == version["source_hash"]
    assert target.read_bytes() == path.read_bytes() == payload


@pytest.mark.parametrize("extension", FORMATS)
@pytest.mark.parametrize("provider", ["local", "google_drive"])
def test_real_pg_mongo_import_bytes_and_readiness(
    source, pg_container, mongo_test_database, tmp_path, extension, provider
):
    c = pg_container
    original = setup_import(source, c, tmp_path)
    path = original.with_name("sample" + extension)
    payload = sample(extension)
    path.write_bytes(payload)
    bucket, key = "books", path.name
    if provider == "google_drive":
        mime = sorted(FormatValidatorRegistry().find_validator(path.name).mime_types)[0]
        settings = c.settings.model_copy(
            update={"source_drive_enabled": True, "source_drive_buckets": {"COURSE_DRIVE": "root"}}
        )
        drive = SourceGoogleDriveFileReader(
            settings, session_factory=lambda: DriveSession(payload, path.name, mime)
        )
        c.source_file_import.files = SourceFileDispatcher(c.source_files, drive)
        bucket, key = "COURSE_DRIVE", "files/blob/" + path.name
    with source.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE resource_versions SET storage_bucket=:bucket, "
                "storage_object_key=:key, source_hash=:hash, updated_at=now()"
            ),
            {"bucket": bucket, "key": key, "hash": sha256(payload).hexdigest()},
        )
    if extension in (".png", ".jpg", ".jpeg"):
        # Deterministic pipeline test, not a claim of real Tesseract recognition.
        c.ocr.engine = ImageEngineDouble()
    uri, name = mongo_test_database
    with MongoClient(uri, serverSelectionTimeoutMS=3000) as client:
        db = client[name]
        initialize(db, apply=True, dimension=384)
        c.mongo_projector.documents = MongoContentStore(db)
        assert c.source_file_worker.once("phase3-formats")
        with c.uow() as repo:
            imported = repo.find("source_file_imports")[0]
            version = repo.get("resource_versions", imported["target_version_id"])
            assert version and imported["status"] == "PROCESSING"
            assert repo.count("content_units", {}) == 0
            assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 0
        target = c.storage.get_local_path(version["storage_bucket"], version["storage_object_key"])
        assert target.read_bytes() == path.read_bytes() == payload
        assert sha256(target.read_bytes()).hexdigest() == version["source_hash"]
        for _ in range(20):
            if not c.worker.once("phase3-formats-ocr"):
                break
        assert c.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
        assert db.mongo_content_units.count_documents({"resource_version_id": version["id"]}) > 0
        assert db.content_bags.count_documents({}) == 0
        assert set(db.list_collection_names()) == set(contracts(384))
        c.sync.run(full=False)
        assert not c.source_file_worker.once("phase3-formats-replay")
        with c.uow() as repo:
            assert repo.count("source_file_imports", {}) == repo.count("resource_versions", {}) == 1
        assert path.read_bytes() == payload


class DriveSession:
    """Only transport double; real format/hash/import/PG/Mongo code is exercised."""

    def __init__(self, payload, name, mime):
        self.payload, self.name, self.mime = payload, name, mime

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, url, **kwargs):
        media = kwargs["params"].get("alt") == "media"
        metadata = (
            dict(id="root", mimeType=FOLDER, trashed=False)
            if url == API + "root"
            else dict(
                id="blob",
                name=self.name,
                mimeType=self.mime,
                size=str(len(self.payload)),
                version="1",
                parents=["root"],
                trashed=False,
                capabilities={"canDownload": True},
            )
        )
        return DriveResponse(
            self.payload if media else json.dumps(metadata).encode(),
            self.mime if media else "application/json",
        )


class DriveResponse:
    status_code = 200

    def __init__(self, payload, mime):
        self.payload, self.headers = payload, {"Content-Type": mime}

    def iter_content(self, size):
        for offset in range(0, len(self.payload), size):
            yield self.payload[offset : offset + size]

    def close(self):
        pass
