from abc import ABC, abstractmethod
from enum import StrEnum

from data_sync_etl.domain.core import DomainError


class MediaCategory(StrEnum):
    DOCUMENT = "DOCUMENT"
    IMAGE = "IMAGE"
    AUDIO = "AUDIO"
    TEXT = "TEXT"
    VIDEO = "VIDEO"


class BaseFormatValidator(ABC):
    @property
    @abstractmethod
    def category(self) -> MediaCategory:
        """Phân loại phương tiện học liệu."""
        ...

    @property
    @abstractmethod
    def supported_mime_types(self) -> set[str]:
        """Tập hợp các MIME type được hỗ trợ."""
        ...

    @property
    @abstractmethod
    def supported_extensions(self) -> set[str]:
        """Tập hợp các phần mở rộng file được hỗ trợ."""
        ...

    @property
    @abstractmethod
    def max_size_bytes(self) -> int:
        """Dung lượng tối đa cho phép tính bằng byte."""
        ...

    @abstractmethod
    def validate_magic_bytes(self, header: bytes) -> bool:
        """Kiểm tra chữ ký nhị phân (magic bytes) ở đầu tệp."""
        ...

    def validate(self, extension: str, mime_type: str, header: bytes, file_size: int) -> None:
        file_is_too_large = file_size > self.max_size_bytes
        if file_is_too_large:
            raise DomainError(f"File size exceeds maximum allowed limit of {self.max_size_bytes} bytes")

        header_is_invalid = not self.validate_magic_bytes(header)
        if header_is_invalid:
            raise DomainError(f"File header signature does not match declared format ({extension})")


class PdfFormatValidator(BaseFormatValidator):
    @property
    def category(self) -> MediaCategory:
        return MediaCategory.DOCUMENT

    @property
    def supported_mime_types(self) -> set[str]:
        return {"application/pdf"}

    @property
    def supported_extensions(self) -> set[str]:
        return {".pdf"}

    @property
    def max_size_bytes(self) -> int:
        return 50 * 1024 * 1024  # 50 MiB

    def validate_magic_bytes(self, header: bytes) -> bool:
        return header.startswith(b"%PDF-")


class ImageFormatValidator(BaseFormatValidator):
    @property
    def category(self) -> MediaCategory:
        return MediaCategory.IMAGE

    @property
    def supported_mime_types(self) -> set[str]:
        return {"image/png", "image/jpeg", "image/webp"}

    @property
    def supported_extensions(self) -> set[str]:
        return {".png", ".jpg", ".jpeg", ".webp"}

    @property
    def max_size_bytes(self) -> int:
        return 20 * 1024 * 1024  # 20 MiB

    def validate_magic_bytes(self, header: bytes) -> bool:
        is_png = header.startswith(b"\x89PNG\r\n\x1a\n")
        is_jpeg = header.startswith(b"\xff\xd8\xff")
        is_webp = header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WEBP"
        return is_png or is_jpeg or is_webp


class AudioFormatValidator(BaseFormatValidator):
    @property
    def category(self) -> MediaCategory:
        return MediaCategory.AUDIO

    @property
    def supported_mime_types(self) -> set[str]:
        return {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/mp4", "audio/x-m4a"}

    @property
    def supported_extensions(self) -> set[str]:
        return {".mp3", ".wav", ".m4a"}

    @property
    def max_size_bytes(self) -> int:
        return 100 * 1024 * 1024  # 100 MiB

    def validate_magic_bytes(self, header: bytes) -> bool:
        if len(header) < 4:
            return False
        is_mp3_id3 = header.startswith(b"ID3")
        is_mp3_sync = header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        is_wav = header.startswith(b"RIFF") and len(header) >= 12 and header[8:12] == b"WAVE"
        is_m4a = len(header) >= 8 and header[4:8] == b"ftyp"
        return is_mp3_id3 or is_mp3_sync or is_wav or is_m4a


class TextFormatValidator(BaseFormatValidator):
    @property
    def category(self) -> MediaCategory:
        return MediaCategory.TEXT

    @property
    def supported_mime_types(self) -> set[str]:
        return {"text/plain", "text/markdown"}

    @property
    def supported_extensions(self) -> set[str]:
        return {".txt", ".md"}

    @property
    def max_size_bytes(self) -> int:
        return 10 * 1024 * 1024  # 10 MiB

    def validate_magic_bytes(self, header: bytes) -> bool:
        try:
            header.decode("utf-8")
            return True
        except UnicodeDecodeError:
            return False


class FormatValidatorRegistry:
    def __init__(self, validators: list[BaseFormatValidator] | None = None) -> None:
        self._validators = validators or [
            PdfFormatValidator(),
            ImageFormatValidator(),
            AudioFormatValidator(),
            TextFormatValidator(),
        ]

    def find_validator(self, extension: str, mime_type: str) -> BaseFormatValidator:
        normalized_ext = extension.lower().strip()
        normalized_mime = mime_type.lower().strip()

        for validator in self._validators:
            ext_matched = normalized_ext in validator.supported_extensions
            mime_matched = normalized_mime in validator.supported_mime_types
            if ext_matched or mime_matched:
                return validator

        raise DomainError(f"Unsupported file format: extension '{extension}', mime '{mime_type}'")

    def is_supported(self, extension: str, mime_type: str) -> bool:
        try:
            self.find_validator(extension, mime_type)
            return True
        except DomainError:
            return False
