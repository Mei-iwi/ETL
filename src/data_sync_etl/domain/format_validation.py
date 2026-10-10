from typing import Protocol

from data_sync_etl.domain.core import DomainError


class FormatValidator(Protocol):
    extension: str
    mime_types: frozenset[str]
    max_size_bytes: int
    media_category: str

    def validate(self, extension: str, mime_type: str, header: bytes, file_size: int) -> None: ...


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
            raise DomainError("Uploaded file exceeds the maximum allowed size")
        if not self._matches_signature(header):
            raise DomainError("File signature does not match the declared file type")

    def _matches_signature(self, header: bytes) -> bool:
        raise NotImplementedError


class PdfFormatValidator(_BaseFormatValidator):
    extension = ".pdf"
    mime_types = frozenset({"application/pdf"})
    max_size_bytes = 50 * 1024 * 1024
    media_category = "DOCUMENT"

    def _matches_signature(self, header: bytes) -> bool:
        return header.startswith(b"%PDF-")


class PngFormatValidator(_BaseFormatValidator):
    extension = ".png"
    mime_types = frozenset({"image/png"})
    max_size_bytes = 20 * 1024 * 1024
    media_category = "IMAGE"

    def _matches_signature(self, header: bytes) -> bool:
        return header.startswith(b"\x89PNG\r\n\x1a\n")


class JpegFormatValidator(_BaseFormatValidator):
    extension = ".jpg"
    mime_types = frozenset({"image/jpeg"})
    max_size_bytes = 20 * 1024 * 1024
    media_category = "IMAGE"

    def _matches_signature(self, header: bytes) -> bool:
        return header.startswith(b"\xff\xd8\xff")


class Mp3FormatValidator(_BaseFormatValidator):
    extension = ".mp3"
    mime_types = frozenset({"audio/mpeg", "audio/mp3"})
    max_size_bytes = 100 * 1024 * 1024
    media_category = "AUDIO"

    def _matches_signature(self, header: bytes) -> bool:
        return header.startswith(b"ID3") or (
            len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        )


class Mp4FormatValidator(_BaseFormatValidator):
    extension = ".mp4"
    mime_types = frozenset({"video/mp4"})
    max_size_bytes = 500 * 1024 * 1024
    media_category = "VIDEO"

    def _matches_signature(self, header: bytes) -> bool:
        return len(header) >= 8 and header[4:8] == b"ftyp"


class FormatValidatorRegistry:
    def __init__(self):
        validators = (
            PdfFormatValidator(),
            PngFormatValidator(),
            JpegFormatValidator(),
            Mp3FormatValidator(),
            Mp4FormatValidator(),
        )
        self._validators = {validator.extension: validator for validator in validators}
        self._validators[".jpeg"] = self._validators[".jpg"]

    def find_validator(self, filename: str) -> FormatValidator:
        from pathlib import PurePath

        extension = PurePath(filename).suffix.lower()
        validator = self._validators.get(extension)
        if validator is None:
            raise DomainError("Unsupported file extension")
        return validator
