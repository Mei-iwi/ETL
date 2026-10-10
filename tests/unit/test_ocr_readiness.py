import subprocess

import pymupdf
import pytest
from conftest import ingest

from data_sync_etl.adapters.ocr.engines import (
    FakeOCREngine,
    TesseractOCREngine,
    UnconfiguredOCREngine,
)
from data_sync_etl.composition import create_ocr_engine
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError


def test_engine_selection_is_explicit():
    assert isinstance(
        create_ocr_engine(Settings(_env_file=None, ocr_engine="native")), UnconfiguredOCREngine
    )
    assert isinstance(create_ocr_engine(Settings(_env_file=None, ocr_engine="fake")), FakeOCREngine)
    assert isinstance(
        create_ocr_engine(Settings(_env_file=None, ocr_engine="tesseract")),
        TesseractOCREngine,
    )


def test_scan_native_mode_never_fakes_success(container, caplog):
    container.ocr.engine = UnconfiguredOCREngine()
    version = ingest(container, [""])
    job = container.ocr.create_job(version["id"])
    for _ in range(container.ocr.max_retries):
        assert container.worker.once("native-only")
    assert not container.worker.once("native-only")
    with container.uow() as repo:
        assert repo.count("ocr_page_results", {}) == 0
        state = repo.get("ocr_jobs", job["id"])
        assert state["failed_pages"] == 1 and state["completed_pages"] == 0
    assert "raw_text" not in caplog.text


def test_encrypted_pdf_rejected(container):
    path = container.storage.get_local_path("input", "encrypted.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        pdf.new_page()
        pdf.save(
            path,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            owner_pw="dummy-owner",
            user_pw="dummy-user",
        )
    with pytest.raises(DomainError, match="Encrypted PDF"):
        container.ocr.pdf.page_count(path)


def test_large_page_raster_is_bounded(container):
    path = container.storage.get_local_path("input", "large.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        pdf.new_page(width=20000, height=9000)
        pdf.save(path)
    image = container.ocr.pdf.render_page_to_image(path, 1, path.with_suffix(".png"))
    pixmap = pymupdf.Pixmap(image)
    assert max(pixmap.width, pixmap.height) <= 4096


def test_tesseract_runtime_and_unicode_result(monkeypatch, tmp_path):
    from data_sync_etl.adapters.ocr import engines

    calls = []
    monkeypatch.setattr(engines.shutil, "which", lambda _: "tesseract")

    def execute(args, **kwargs):
        calls.append(args)
        if "--version" in args:
            return subprocess.CompletedProcess(args, 0, "tesseract 5.5.0\n", "")
        if "--list-langs" in args:
            return subprocess.CompletedProcess(
                args, 0, "List of available languages (2):\neng\nvie\n", ""
            )
        return subprocess.CompletedProcess(args, 0, "Tiếng Việt\nEnglish\n", "")

    monkeypatch.setattr(engines.subprocess, "run", execute)
    result = TesseractOCREngine(timeout=5).recognize(tmp_path / "page.png")
    assert result.text == "Tiếng Việt\nEnglish\n"
    assert result.engine_name == "tesseract"
    assert result.engine_version == "tesseract 5.5.0"
    assert calls[-1][-2:] == ["-l", "vie+eng"]


def test_tesseract_missing_runtime_or_language(monkeypatch, tmp_path):
    from data_sync_etl.adapters.ocr import engines

    monkeypatch.setattr(engines.shutil, "which", lambda _: None)
    with pytest.raises(DomainError, match="BLOCKED_OCR_RUNTIME"):
        TesseractOCREngine().recognize(tmp_path / "page.png")

    monkeypatch.setattr(engines.shutil, "which", lambda _: "tesseract")
    monkeypatch.setattr(
        engines.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "eng\n", ""),
    )
    with pytest.raises(DomainError, match="language data missing"):
        TesseractOCREngine().recognize(tmp_path / "page.png")


def test_mixed_page_keeps_short_native_text_and_ocr_scan(container, tmp_path):
    image = tmp_path / "scan.png"
    with pymupdf.open() as raster:
        page = raster.new_page()
        page.draw_rect(pymupdf.Rect(0, 0, 400, 400), color=(0, 0, 0))
        page.get_pixmap().save(image)
    version = ingest(container, ["x"])
    path = container.storage.get_local_path("input", "sample.pdf")
    with pymupdf.open() as pdf:
        page = pdf.new_page()
        page.insert_text((72, 72), "Header")
        page.insert_image(pymupdf.Rect(72, 100, 450, 600), filename=str(image))
        pdf.save(path)
    # Ingestion snapshots the file; create a new version for the mixed PDF.
    version = container.ingestion.run("resource-1", "input", "sample.pdf")["version"]
    job = container.ocr.create_job(version["id"])
    assert container.worker.once("mixed")
    with container.uow() as repo:
        task = repo.find("ocr_page_tasks", {"job_id": job["id"]})[0]
        result = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
        assert "Header" in result["raw_text"]
        assert "Demo OCR text" in result["raw_text"]
        assert result["result_metadata"]["mixed_native_text"] is True


@pytest.mark.parametrize("page_count", [1, 3])
def test_scanned_pages_use_ocr_engine_and_persist_unicode(container, page_count):
    container.ocr.engine = FakeOCREngine("Tiếng Việt trên trang scan")
    version = ingest(container, [""] * page_count)
    job = container.ocr.create_job(version["id"])
    for _ in range(page_count):
        assert container.worker.once("scanner")
    with container.uow() as repo:
        assert repo.get("ocr_jobs", job["id"])["status"] == "COMPLETED"
        for task in repo.find("ocr_page_tasks", {"job_id": job["id"]}):
            result = repo.find("ocr_page_results", {"page_task_id": task["id"]})[0]
            assert result["raw_text"] == "Tiếng Việt trên trang scan"
            assert result["engine_name"] == "fake"
