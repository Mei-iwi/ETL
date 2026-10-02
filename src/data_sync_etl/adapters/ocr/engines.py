import pymupdf

from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import OCRResult


class PyMuPDFPDFProcessor:
    def page_count(self, path):
        with pymupdf.open(path) as pdf:
            if pdf.needs_pass:
                raise DomainError("Encrypted PDF is unsupported")
            return len(pdf)

    def render_page_to_image(self, path, page_num, output):
        with pymupdf.open(path) as pdf:
            page = pdf[page_num - 1]
            # Cap the raster dimensions to avoid unbounded images on oversized pages.
            scale = min(2.0, 4096 / max(page.rect.width, page.rect.height))
            page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).save(output)
        return output

    def extract_native_text(self, path, page_num):
        with pymupdf.open(path) as pdf:
            return pdf[page_num - 1].get_text()


class FakeOCREngine:
    """Explicit test/demo engine; never selected by the default configuration."""

    def __init__(self, text="Demo OCR text"):
        self.text = text

    def recognize(self, image_path):
        return OCRResult(self.text, 1.0, "fake", "1", {"demo": True})


class UnconfiguredOCREngine:
    def recognize(self, image_path):
        raise DomainError("Scanned page requires a configured OCR engine")
