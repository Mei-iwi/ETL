"""Opt-in real 384D model + Tesseract on fresh SOURCE/Master/Mongo test fixtures."""

import io
import json
import os
from hashlib import sha256
from math import isfinite
from zipfile import ZipFile

import pymupdf
import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient
from sqlalchemy import text

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import contracts, initialize
from data_sync_etl.adapters.ocr.engines import TesseractOCREngine
from data_sync_etl.adapters.storage.evaluation_store import EvaluationFileStore
from data_sync_etl.adapters.storage.source_dispatcher import SourceFileDispatcher
from data_sync_etl.adapters.storage.source_google_drive import SourceGoogleDriveFileReader
from data_sync_etl.application.evaluation import EvaluationService
from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.application.search import SearchService
from data_sync_etl.config import Settings
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.main import create_app
from tests.unit.test_multiformat import FORMATS, sample

from .test_file_import_flow import setup_import
from .test_sync_local_drive_reconciliation import DriveSession

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        os.environ.get("ETL_TEST_REAL_EMBEDDING") != "1",
        reason="Requires explicitly provisioned real local embedding/OCR runtime",
    ),
]

QUERIES = (
    "phân số",
    "phép cộng",
    "phép trừ",
    "bài học phân số",
    "cách cộng hai phân số",
    "tính hiệu các phân số",
    "phép toán với phân số",
    "bài học toán",
    "ví dụ cộng trừ",
    "cộng số hữu tỉ",
    "trừ số hữu tỉ",
    "tìm tài liệu học phân số",
    "thực hành phép cộng",
    "luyện tập phép trừ",
    "phép cộng và phép trừ",
    "cộng khác mẫu",
    "cộng cùng mẫu",
    "bài tập tiếng Việt",
)


@pytest.fixture(scope="module")
def real_embedding():
    import torch

    config = Settings(_env_file=None)
    torch.set_num_threads(2)
    model = LocalSentenceEmbedding(
        config.index_embedding_model_path,
        config.index_embedding_model_id,
        config.index_embedding_model_version,
        config.index_embedding_dimension,
    )
    assert model.available(), "Provision the real local model before opting in"
    model.prepare()
    assert model.dimension == 384
    return model


@pytest.mark.parametrize("provider", ["local", "google_drive"])
@pytest.mark.parametrize("extension", [*FORMATS, ".scan.pdf"])
def test_real_content_to_bags_search_and_evaluation(
    source,
    pg_container,
    mongo_test_database,
    tmp_path,
    real_embedding,
    monkeypatch,
    provider,
    extension,
):
    c = pg_container
    original = setup_import(source, c, tmp_path)
    path = original.with_name("acceptance" + extension)
    if extension == ".scan.pdf":
        with pymupdf.open() as document:
            page = document.new_page(width=1000, height=200)
            page.insert_image(page.rect, stream=sample(".png"))
            payload = document.tobytes()
        mime_extension = ".pdf"
    else:
        payload, mime_extension = sample(extension), extension
    path.write_bytes(payload)
    if extension in (".docx", ".xlsx", ".pptx"):
        assert payload.startswith(b"PK")
        part = {
            ".docx": "word/document.xml",
            ".xlsx": "xl/workbook.xml",
            ".pptx": "ppt/presentation.xml",
        }[extension]
        with ZipFile(io.BytesIO(payload)) as archive:
            assert part in archive.namelist() and "[Content_Types].xml" in archive.namelist()
    bucket, key = "books", path.name
    if provider == "google_drive":
        mime = sorted(FormatValidatorRegistry().find_validator("file" + mime_extension).mime_types)[
            0
        ]
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
    config = Settings(_env_file=None)
    c.ocr.engine = TesseractOCREngine(
        config.tesseract_path, config.tesseract_languages, config.ocr_timeout_seconds
    )
    c.settings.index_embedding_dimension = 384
    c.settings.index_auto_enabled = False
    c.settings.search_internal_dev_enabled = True
    c.settings.app_environment = "development"
    c.settings.admin_enabled = True
    monkeypatch.setattr(
        "sys.argv", ["uvicorn/__main__.py", "app", "--host", "127.0.0.1", "--no-proxy-headers"]
    )
    monkeypatch.setattr("data_sync_etl.api.search_access.container_runtime", lambda: False)
    with MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000) as client:
        db = client[mongo_test_database[1]]
        assert initialize(db, apply=True, dimension=384)["ready"]
        c.mongo_projector.documents = MongoContentStore(db)
        c.indexing = IndexingService(
            c.uow, MongoContentReader(db), real_embedding, c.settings, bags=MongoBagStore(db, 384)
        )
        c.processing_finalizer.on_content_ready = c.indexing.enqueue_auto
        c.search = SearchService(c.indexing, c.settings, c.indexing.bags)
        c.evaluations = EvaluationService(
            c.search, EvaluationFileStore(tmp_path / "reports"), c.settings
        )
        assert c.source_file_worker.once("phase4-import")
        with c.uow() as repo:
            imported = repo.find("source_file_imports")[0]
            version = repo.get("resource_versions", imported["target_version_id"])
            assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 0
        for _ in range(30):
            if not c.worker.once("phase4-content"):
                break
        assert c.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
        target = c.storage.get_local_path(version["storage_bucket"], version["storage_object_key"])
        assert target.read_bytes() == path.read_bytes() == payload
        assert sha256(target.read_bytes()).hexdigest() == version["source_hash"]
        with TestClient(
            create_app(c), base_url="http://127.0.0.1", client=("127.0.0.1", 50000)
        ) as api:
            base = "/api/v1/resource-versions/" + version["id"]
            assert api.post(base + "/index").status_code == 202
            assert c.worker.once("phase4-index")
            assert api.get(base + "/index-status").json()["index_status"] == "READY"
            bags = list(db.content_bags.find({}))
            assert bags
            assert all(
                len(bag["embedding"]) == 384
                and any(bag["embedding"])
                and all(isfinite(x) for x in bag["embedding"])
                for bag in bags
            )
            assert all(
                len(unit.get("keywords") or []) <= 5 for unit in db.mongo_content_units.find({})
            )
            assert api.post(base + "/index").json()["action"] == "ALREADY_INDEXED"
            assert api.post(base + "/reindex").status_code == 202
            assert c.worker.once("phase4-reindex")
            assert api.get(base + "/index-status").json()["index_status"] == "READY"
            for method in ("bow", "vector", "hybrid"):
                response = api.post(
                    "/api/v1/search", json={"query": bags[0]["keywords"][0], "method": method}
                )
                assert response.status_code == 200, response.text
                assert response.json()["total"] > 0
                context = response.json()["items"][0]["context_id"]
                assert api.get("/api/v1/chunks/" + context + "/context").status_code == 200
            assert api.get(base + "/chunks").json()["total"] > 0
            print(
                "REAL_PIPELINE "
                + json.dumps(
                    {
                        "provider": provider,
                        "format": extension,
                        "processing_status": "CONTENT_READY",
                        "index_status": "READY",
                        "dimension": 384,
                        "bags": len(bags),
                        "search_context": "PASS",
                        "index_reindex": "PASS",
                        "sha256_parity": True,
                    },
                    sort_keys=True,
                )
            )
            if extension == ".docx" and provider == "local":
                # Deliberately unconfirmed fixture labels, never inferred from Search rankings.
                command = {
                    "name": "Phase04 runtime acceptance, NOT ground truth",
                    "ground_truth_confirmed": False,
                    "queries": [
                        {
                            "query_id": "q" + str(i + 1),
                            "query": query,
                            "relevance": [{"item_id": version["resource_id"], "grade": 0}],
                        }
                        for i, query in enumerate(QUERIES)
                    ],
                }
                created = api.post("/api/v1/evaluations/datasets", json=command)
                assert created.status_code == 201, created.text
                run = api.post(
                    "/api/v1/evaluations/runs",
                    json={"dataset_id": created.json()["dataset_id"], "k_values": [1, 3, 5]},
                )
                assert run.status_code == 201 and run.json()["status"] == "COMPLETED", run.text
                detail = api.get("/api/v1/evaluations/runs/" + run.json()["run_id"]).json()
                assert detail["pagination"]["total"] == 54
                assert "LABELS_NOT_HUMAN_CONFIRMED" in detail["warnings"]
                assert all(
                    row["successful_queries"] == 18 and row["failed_queries"] == 0
                    for row in detail["summary"].values()
                )
                assert all(
                    row["metrics"]["1"]["recall"]["value"] is None
                    for row in detail["summary"].values()
                )
                assert (
                    api.get(
                        "/api/v1/evaluations/runs/" + run.json()["run_id"] + "/report"
                    ).status_code
                    == 200
                )
                print("REAL_EVALUATION 18x3=54 PASS; k=1,3,5; labels=UNCONFIRMED; quality=NOT_RUN")
                print(
                    "REAL_EVALUATION_EVIDENCE "
                    + json.dumps(
                        {
                            "status": detail["status"],
                            "quality": "NOT_RUN",
                            "ground_truth_confirmed": False,
                            "observations": 54,
                            "k_values": [1, 3, 5],
                            "methods": {
                                method: {
                                    "successful": summary["successful_queries"],
                                    "failed": summary["failed_queries"],
                                    "latency_ms": summary["latency_ms"],
                                    "recall": summary["metrics"]["1"]["recall"]["value"],
                                }
                                for method, summary in detail["summary"].items()
                            },
                        },
                        sort_keys=True,
                    )
                )
                # Reproduce 18/18 BoW versus 0/18 Vector/Hybrid without altering
                # stored bags or labels. The runtime outage is explicitly injected.
                with monkeypatch.context() as outage:

                    def unavailable(*args, **kwargs):
                        raise RuntimeError("PRIVATE_INFERENCE_FAILURE")

                    outage.setattr(real_embedding._model, "encode", unavailable)
                    failed = api.post(
                        "/api/v1/evaluations/runs",
                        json={"dataset_id": created.json()["dataset_id"], "k_values": [1, 3, 5]},
                    )
                    assert failed.status_code == 201 and failed.json()["status"] == "PARTIAL"
                    audit = c.evaluations.store.get("runs", failed.json()["run_id"])
                    assert audit["summary"]["bow"]["successful_queries"] == 18
                    for method in ("vector", "hybrid"):
                        summary = audit["summary"][method]
                        assert (
                            summary["successful_queries"] == 0 and summary["failed_queries"] == 18
                        )
                        assert summary["metrics"]["1"]["precision"]["value"] is None
                        assert all(
                            row["error_code"] == "BLOCKED_EMBEDDING_RUNTIME"
                            for row in audit["observations"]
                            if row["method"] == method
                        )
                    assert "PRIVATE" not in str(audit)
                recovered = api.post(
                    "/api/v1/search", json={"query": bags[0]["keywords"][0], "method": "hybrid"}
                )
                assert recovered.status_code == 200 and recovered.json()["total"] > 0
                print(
                    "INJECTED_RUNTIME_OUTAGE bow=18 vector=0 hybrid=0; safe_codes=PASS recovery=PASS"
                )
        assert set(db.list_collection_names()) == set(contracts(384))
        assert path.read_bytes() == payload
