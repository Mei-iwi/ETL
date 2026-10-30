import shutil
import subprocess
import tempfile
from pathlib import Path

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

    def has_significant_image(self, path, page_num):
        with pymupdf.open(path) as pdf:
            page = pdf[page_num - 1]
            area = page.rect.get_area()
            return any(
                pymupdf.Rect(item["bbox"]).get_area() >= area * 0.1
                for item in page.get_image_info()
            )


class TesseractOCREngine:
    supports_images = True

    def check_runtime(self):
        if self._version is None:
            self._runtime()

    def __init__(self, executable="tesseract", languages="vie+eng", timeout=60):
        self.executable = executable
        self.languages = languages
        self.timeout = timeout
        self._version = None

    def _runtime(self):
        executable = shutil.which(self.executable)
        if not executable and not Path(self.executable).is_file():
            raise DomainError("BLOCKED_OCR_RUNTIME: Tesseract executable not found")
        try:
            version = subprocess.run(
                [self.executable, "--version"],
                capture_output=True,
                text=True,
                timeout=min(self.timeout, 10),
                check=True,
            )
            languages = subprocess.run(
                [self.executable, "--list-langs"],
                capture_output=True,
                text=True,
                timeout=min(self.timeout, 10),
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            raise DomainError("BLOCKED_OCR_RUNTIME: Tesseract runtime check failed") from None
        available = set(languages.stdout.splitlines()[1:])
        if not set(self.languages.split("+")).issubset(available):
            raise DomainError("BLOCKED_OCR_RUNTIME: required Tesseract language data missing")
        self._version = version.stdout.splitlines()[0].strip() if version.stdout else "unknown"

    def recognize(self, image_path):
        return self._recognize(image_path)

    def recognize_bytes(self, image_bytes):
        """Run Tesseract through stdin; this path creates no image/temp file."""
        if self._version is None:
            self._runtime()
        try:
            executable = str(Path(shutil.which(self.executable) or self.executable).resolve())
            result = subprocess.run(
                [executable, 'stdin', 'stdout', '-l', self.languages],
                input=image_bytes, capture_output=True, text=False,
                timeout=self.timeout, check=True,
            )
            text = result.stdout.decode('utf-8', errors='replace')
        except subprocess.TimeoutExpired:
            raise DomainError('Tesseract OCR timed out') from None
        except (OSError, subprocess.CalledProcessError):
            raise DomainError('Tesseract OCR failed') from None
        return OCRResult(text, engine_name='tesseract', engine_version=self._version,
                         metadata={'languages': self.languages, 'input_mode': 'stdin'})

    def recognize_scoped(self, image_path, *, directory):
        """Keep SOURCE subprocess scratch inside the caller's leased attempt."""
        return self._recognize(image_path, directory=directory)

    def _recognize(self, image_path, directory=None):
        if self._version is None:
            self._runtime()
        try:
            executable = str(Path(shutil.which(self.executable) or self.executable).resolve())
            with tempfile.TemporaryDirectory(prefix="etl-ocr-", dir=directory) as temp_dir:
                temp_image = Path(temp_dir) / "input.png"
                shutil.copyfile(image_path, temp_image)
                # A relative ASCII filename avoids Windows native-library failures
                # on Vietnamese paths, even if the TEMP parent itself has Unicode.
                result = subprocess.run(
                    [executable, "input.png", "stdout", "-l", self.languages],
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=self.timeout,
                    check=True,
                )
        except subprocess.TimeoutExpired:
            raise DomainError("Tesseract OCR timed out") from None
        except (OSError, subprocess.CalledProcessError):
            raise DomainError("Tesseract OCR failed") from None
        return OCRResult(
            result.stdout,
            engine_name="tesseract",
            engine_version=self._version,
            metadata={"languages": self.languages},
        )


class FakeOCREngine:
    """Explicit test/demo engine; never selected by the default configuration."""

    def __init__(self, text="Demo OCR text"):
        self.text = text

    def recognize(self, image_path):
        return OCRResult(self.text, 1.0, "fake", "1", {"demo": True})


class UnconfiguredOCREngine:
    def recognize(self, image_path):
        raise DomainError("BLOCKED_OCR_RUNTIME: scanned page requires a configured OCR engine")
