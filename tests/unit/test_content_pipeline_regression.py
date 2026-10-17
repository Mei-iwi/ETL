"""Prompt 04 edge regressions; no real databases, model or OCR required."""

from unittest.mock import Mock

import pytest
from conftest import ingest

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.ocr.engines import FakeOCREngine
from data_sync_etl.domain.indexing import IndexingError


@pytest.mark.parametrize("where", ["probe", "query"])
def test_embedding_inference_failure_is_a_safe_runtime_blocker(tmp_path, monkeypatch, where):
    embedding = LocalSentenceEmbedding(tmp_path, "fixture-model", "v1", 384)
    monkeypatch.setattr(embedding, "available", lambda: True)
    embedding._model = Mock()
    embedding._checked = where == "query"
    embedding._model.encode.side_effect = RuntimeError("PRIVATE_MODEL_PATH_OR_PROVIDER_DETAIL")
    with pytest.raises(IndexingError, match="BLOCKED_EMBEDDING_RUNTIME") as failure:
        embedding.prepare() if where == "probe" else embedding.embed("Phan so")
    assert "PRIVATE" not in str(failure.value)
    assert embedding._checked is False


@pytest.mark.parametrize("text", ["", "   \n\t"])
def test_empty_scan_fails_in_ocr_without_publishing_content(container, text):
    container.ocr.engine = FakeOCREngine(text)
    version = ingest(container, [""])
    job = container.ocr.create_job(version["id"])
    assert container.worker.once("empty-scan")
    with container.uow() as repo:
        task = repo.find("ocr_page_tasks", {"job_id": job["id"]})[0]
        assert task["status"] == "FAILED"
        assert task["error_message"] == "EMPTY_EXTRACTED_CONTENT"
        assert task["retry_count"] == 1
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED_WITH_ERRORS"
        assert repo.count("ocr_page_results", {}) == 0
        assert repo.count("content_units", {}) == 0
        assert repo.count("etl_stage_runs", {"stage": "INDEX_BUILD"}) == 0
    assert container.processing_job_detail.run(job["id"])["error_code"] == "EMPTY_EXTRACTED_CONTENT"


@pytest.mark.parametrize("extension", [".docx", ".xlsx", ".pptx"])
def test_openxml_changed_after_import_is_rejected_before_parser(container, extension, monkeypatch):
    from tests.unit.test_multiformat import sample

    container.sync.run()
    path = container.storage.get_local_path("input", "original" + extension)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sample(extension))
    version = container.ingestion.run("resource-1", "input", path.name)["version"]
    job = container.ocr.create_job(version["id"])
    stored = container.storage.get_local_path(
        version["storage_bucket"], version["storage_object_key"]
    )
    stored.write_bytes(b"changed imported bytes, not a ZIP")
    monkeypatch.setattr(
        container.ocr.native_documents,
        "extract",
        Mock(side_effect=AssertionError("Parser must not read changed bytes")),
    )
    assert container.worker.once("changed-openxml")
    with container.uow() as repo:
        task = repo.find("ocr_page_tasks", {"job_id": job["id"]})[0]
        assert task["status"] == "FAILED" and task["retry_count"] == 1
        assert task["error_message"] == "SOURCE_HASH_MISMATCH"
        assert repo.count("ocr_page_results", {}) == repo.count("content_units", {}) == 0
        assert repo.get("resource_versions", version["id"])["source_hash"] == version["source_hash"]
    assert container.processing_job_detail.run(job["id"])["error_code"] == "SOURCE_HASH_MISMATCH"


def test_mixed_pdf_keeps_valid_native_text_when_ocr_is_empty(container, monkeypatch):
    container.ocr.engine = FakeOCREngine("")
    version = ingest(container, ["Header"])
    monkeypatch.setattr(container.ocr.pdf, "has_significant_image", lambda *args: True)
    job = container.ocr.create_job(version["id"])
    assert container.worker.once("mixed-empty-ocr")
    with container.uow() as repo:
        task = repo.find("ocr_page_tasks", {"job_id": job["id"]})[0]
        assert task["status"] == "SUCCEEDED"
        result = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
        assert result["raw_text"].strip() == "Header"
        assert result["result_metadata"]["mixed_native_text"] is True
        assert result["result_metadata"]["ocr_empty"] is True
    assert container.orchestrator.status(version["id"])["processing_status"] == "CONTENT_READY"
