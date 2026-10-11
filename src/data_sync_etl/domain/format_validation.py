import mmap
from pathlib import Path
from typing import Protocol

import pymupdf

from data_sync_etl.domain.core import DomainError, UploadTooLargeError


class InvalidFileContent(DomainError):
    pass


class FormatValidator(Protocol):
    extension: str
    max_size_bytes: int
    media_category: str

    def validate(self, extension: str, mime_type: str, header: bytes, file_size: int) -> None: ...
    def validate_path(self, path: Path) -> None: ...


class _BaseFormatValidator:
    extension = ""
    mime_types: frozenset[str] = frozenset()
    max_size_bytes = 0
    media_category = ""

    def validate(self, extension: str, mime_type: str, header: bytes, file_size: int) -> None:
        if extension.lower() != self.extension:
            raise DomainError("File extension does not match its content type")
        if mime_type.lower().split(";", 1)[0].strip() not in self.mime_types:
            raise DomainError("Unsupported or mismatched MIME type")
        if file_size < 1:
            raise DomainError("Uploaded file is empty")
        if file_size > self.max_size_bytes:
            raise UploadTooLargeError("Uploaded file exceeds the maximum allowed size")
        if not self._matches_signature(header):
            raise DomainError("File signature does not match the declared file type")

    def _matches_signature(self, header: bytes) -> bool:
        raise NotImplementedError

    def validate_path(self, path: Path) -> None:
        # Signature-only validation for formats without a configured parser.
        return None

class PdfFormatValidator(_BaseFormatValidator):
    extension = ".pdf"
    mime_types = frozenset({"application/pdf"})
    max_size_bytes = 50 * 1024 * 1024
    media_category = "DOCUMENT"

    def _matches_signature(self, header):
        return header.startswith(b"%PDF-")

    def validate_path(self, path: Path) -> None:
        try:
            with path.open("rb") as source:
                with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                    view = memoryview(mapped)
                    try:
                        with pymupdf.open(stream=view, filetype="pdf") as document:
                            if document.needs_pass or document.page_count < 1:
                                raise InvalidFileContent("PDF is encrypted or has no pages")
                            for page_number in range(document.page_count):
                                document.load_page(page_number)
                    finally:
                        view.release()
        except (pymupdf.FileDataError, RuntimeError, ValueError, OSError):
            raise InvalidFileContent("PDF cannot be opened") from None


class PngFormatValidator(_BaseFormatValidator):
    extension = ".png"
    mime_types = frozenset({"image/png"})
    max_size_bytes = 20 * 1024 * 1024
    media_category = "IMAGE"

    def _matches_signature(self, header):
        return header.startswith(b"\x89PNG\r\n\x1a\n")


class JpegFormatValidator(_BaseFormatValidator):
    extension = ".jpg"
    mime_types = frozenset({"image/jpeg"})
    max_size_bytes = 20 * 1024 * 1024
    media_category = "IMAGE"

    def _matches_signature(self, header):
        return header.startswith(b"\xff\xd8\xff")


class Mp3FormatValidator(_BaseFormatValidator):
    extension = ".mp3"
    mime_types = frozenset({"audio/mpeg", "audio/mp3"})
    max_size_bytes = 100 * 1024 * 1024
    media_category = "AUDIO"

    def _matches_signature(self, header):
        return header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0)


class Mp4FormatValidator(_BaseFormatValidator):
    extension = ".mp4"
    mime_types = frozenset({"video/mp4"})
    max_size_bytes = 500 * 1024 * 1024
    media_category = "VIDEO"

    def _matches_signature(self, header):
        return len(header) >= 8 and header[4:8] == b"ftyp"


class FormatValidatorRegistry:
    def __init__(self):
        validators = (PdfFormatValidator(), PngFormatValidator(), JpegFormatValidator(), Mp3FormatValidator(), Mp4FormatValidator())
        self._validators = {validator.extension: validator for validator in validators}
        self._validators[".jpeg"] = self._validators[".jpg"]

    def find_validator(self, filename):
        from pathlib import PurePath
        validator = self._validators.get(PurePath(filename).suffix.lower())
        if validator is None:
            raise DomainError("Unsupported file extension")
        return validator
