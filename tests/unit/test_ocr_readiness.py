import pymupdf
import pytest
from conftest import ingest
from pydantic import ValidationError

from data_sync_etl.adapters.ocr.engines import FakeOCREngine, UnconfiguredOCREngine
from data_sync_etl.composition import create_ocr_engine
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError


def test_engine_selection_is_explicit():
    assert isinstance(
        create_ocr_engine(Settings(_env_file=None, ocr_engine="native")), UnconfiguredOCREngine
    )
    assert isinstance(create_ocr_engine(Settings(_env_file=None, ocr_engine="fake")), FakeOCREngine)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ocr_engine="tesseract")


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
