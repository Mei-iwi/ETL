import mmap
import warnings
from pathlib import Path
from typing import Protocol
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import pymupdf

from data_sync_etl.domain.core import DomainError, UploadTooLargeError


class InvalidFileContent(DomainError):
    pass


# Read-only compatibility for errors persisted by the former media processor.
LEGACY_MEDIA_ERROR_CODES = {'MEDIA_NO_AUDIO', 'NO_SPEECH_CONTENT', 'MEDIA_DURATION_EXCEEDED',
                            'INVALID_MEDIA', 'STT_RESULT_INVALID', 'STT_OUTPUT_LIMIT_EXCEEDED',
                            'STT_RUNTIME_UNAVAILABLE', 'STT_TIMEOUT', 'STT_BUSY', 'STT_OPERATION_FAILED'}

# Only stable, sanitized codes may leave persisted worker task diagnostics.
PUBLIC_PROCESSING_ERROR_CODES = LEGACY_MEDIA_ERROR_CODES | {
    'EMPTY_EXTRACTED_CONTENT', 'SOURCE_HASH_MISMATCH', 'BLOCKED_OCR_RUNTIME',
    'MEDIA_RUNTIME_UNAVAILABLE', 'MEDIA_TIMEOUT', 'MEDIA_DECODE_FAILED', 'INVALID_MEDIA_TRACK',
    'MODEL_RUNTIME_UNAVAILABLE', 'MEDIA_SIZE_EXCEEDED',
    'MEDIA_CAPACITY_TIMEOUT', 'STT_MULTILINGUAL_MODEL_REQUIRED',
}


class ProcessingFormatError(DomainError):
    """Public safe code, without parser details or local paths."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


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
    text_extraction_supported = False
    ocr_requires_runtime = False

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
    text_extraction_supported = True

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
    text_extraction_supported = True
    ocr_requires_runtime = True

    def _matches_signature(self, header):
        return header.startswith(b"\x89PNG\r\n\x1a\n")

    def validate_path(self, path):
        validate_image(path, "PNG")


class JpegFormatValidator(_BaseFormatValidator):
    extension = ".jpg"
    mime_types = frozenset({"image/jpeg"})
    max_size_bytes = 20 * 1024 * 1024
    media_category = "IMAGE"
    text_extraction_supported = True
    ocr_requires_runtime = True

    def _matches_signature(self, header):
        return header.startswith(b"\xff\xd8\xff")

    def validate_path(self, path):
        validate_image(path, "JPEG")


MAX_IMAGE_PIXELS = 20_000_000


def validate_image(path, expected_format):
    from PIL import Image
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format != expected_format or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise InvalidFileContent("INVALID_IMAGE: unsupported image or pixel limit exceeded")
                image.verify()
            # JPEG verify() only inspects the container; bounded decode also
            # rejects truncated/corrupt pixel data before publishing the upload.
            with Image.open(path) as image:
                image.load()
    except InvalidFileContent:
        raise
    except Exception:
        raise InvalidFileContent("INVALID_IMAGE: unreadable image or pixel limit exceeded") from None


class OpenXMLFormatValidator(_BaseFormatValidator):
    max_size_bytes = 20 * 1024 * 1024
    media_category = "DOCUMENT"
    text_extraction_supported = True

    def __init__(self, extension, mime_type, main_part, content_type):
        self.extension = extension
        self.mime_types = frozenset({mime_type})
        self.main_part = main_part
        self.content_type = content_type

    def _matches_signature(self, header):
        return header.startswith(b"PK\x03\x04")

    def validate_path(self, path):
        try:
            with ZipFile(path) as archive:
                entries = archive.infolist()
                if len(entries) > 2000 or sum(item.file_size for item in entries) > 64 * 1024 * 1024:
                    raise InvalidFileContent("OPENXML_LIMIT_EXCEEDED")
                names = set()
                for item in entries:
                    name = item.filename
                    if (name in names or name.startswith('/') or '\\' in name or ':' in name
                            or any(part in {'.', '..'} for part in name.split('/')) or item.flag_bits & 1
                            or (item.external_attr >> 16) & 0o170000 == 0o120000):
                        raise InvalidFileContent("INVALID_OPENXML_CONTAINER")
                    names.add(name)
                    if (item.file_size > 20 * 1024 * 1024
                            or item.file_size > max(1024 * 1024, item.compress_size * 200)):
                        raise InvalidFileContent("OPENXML_LIMIT_EXCEEDED")
                if self.main_part not in names or '[Content_Types].xml' not in names:
                    raise InvalidFileContent("OPENXML_TYPE_MISMATCH")
                content_types = None
                for item in entries:
                    if item.filename.endswith(('.xml', '.rels')):
                        data = archive.read(item)
                        if b'\x00' in data or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                            raise InvalidFileContent("INVALID_OPENXML_XML")
                        node = ElementTree.fromstring(data)
                        if item.filename == '[Content_Types].xml':
                            content_types = node
                    elif not item.is_dir():
                        with archive.open(item) as stream:
                            while stream.read(65536):
                                pass  # Verify CRC without loading embedded binary parts.
                    if 'vbaproject' in item.filename.lower():
                        raise InvalidFileContent("UNSUPPORTED_OPENXML_MACROS")
                namespace = '{http://schemas.openxmlformats.org/package/2006/content-types}'
                if content_types is None or content_types.tag != namespace + 'Types':
                    raise InvalidFileContent("OPENXML_TYPE_MISMATCH")
                # OPC permits a part type to come from either an explicit
                # Override or its extension's Default. An Override takes
                # precedence: never mask a wrong type with a valid Default.
                overrides = [node.get('ContentType') for node in content_types
                             if node.tag == namespace + 'Override'
                             and node.get('PartName') == '/' + self.main_part]
                defaults = [node.get('ContentType') for node in content_types
                            if node.tag == namespace + 'Default'
                            and node.get('Extension', '').lower() == Path(self.main_part).suffix[1:].lower()]
                declared_types = overrides if overrides else defaults
                if declared_types != [self.content_type]:
                    raise InvalidFileContent("OPENXML_TYPE_MISMATCH")
        except InvalidFileContent:
            raise
        except (BadZipFile, OSError, ValueError, RuntimeError, NotImplementedError, ElementTree.ParseError):
            raise InvalidFileContent("INVALID_OPENXML_CONTAINER") from None


class TextFormatValidator(_BaseFormatValidator):
    max_size_bytes = 10 * 1024 * 1024
    media_category = "DOCUMENT"
    text_extraction_supported = True

    def __init__(self, extension, mime_types):
        self.extension, self.mime_types = extension, frozenset(mime_types)

    def _matches_signature(self, header):
        return b'\x00' not in header

    def validate_path(self, path):
        try:
            with path.open(encoding='utf-8-sig', newline='') as stream:
                while block := stream.read(65536):
                    if '\x00' in block:
                        raise InvalidFileContent("INVALID_TEXT: null byte")
        except UnicodeError:
            raise InvalidFileContent("UNSUPPORTED_TEXT_ENCODING: UTF-8 required") from None


class FormatValidatorRegistry:
    def __init__(self, *, media_registration=False, media_processing=None,
                 media_probe=None, media_max_size_bytes=50 * 1024 * 1024):
        if media_processing is None:
            media_processing = not media_registration
        validators = (
            PdfFormatValidator(), PngFormatValidator(), JpegFormatValidator(),
            OpenXMLFormatValidator('.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                                   'word/document.xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml'),
            OpenXMLFormatValidator('.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                                   'xl/workbook.xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'),
            OpenXMLFormatValidator('.pptx', 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                                   'ppt/presentation.xml', 'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml'),
            TextFormatValidator('.txt', {'text/plain'}),
            TextFormatValidator('.csv', {'text/csv', 'application/csv', 'text/plain', 'application/vnd.ms-excel'}),
        )
        self._validators = {validator.extension: validator for validator in validators}
        self._validators[".jpeg"] = self._validators[".jpg"]
        if media_registration or media_processing:
            from data_sync_etl.domain.media_registration import (
                Mp3RegistrationValidator,
                Mp4RegistrationValidator,
            )
            for validator in (Mp3RegistrationValidator(), Mp4RegistrationValidator()):
                if media_processing:
                    validator.text_extraction_supported = True
                    validator.processing_probe = media_probe
                    validator.max_size_bytes = media_max_size_bytes
                self._validators[validator.extension] = validator

    def describe(self):
        return [{
            'extension': extension, 'mime_type': 'text/csv' if extension == '.csv' else sorted(validator.mime_types)[0],
            'mime_types': sorted(validator.mime_types),
            'upload_accepted': True, 'text_extraction_supported': validator.text_extraction_supported,
            'ocr_requires_runtime': validator.ocr_requires_runtime,
            'native_text_extraction': validator.text_extraction_supported and not validator.ocr_requires_runtime and extension not in ('.mp3', '.mp4'),
            'native_text_extractioin': validator.text_extraction_supported and not validator.ocr_requires_runtime and extension not in ('.mp3', '.mp4'),
            'max_size_bytes': validator.max_size_bytes,
        } for extension, validator in self._validators.items()]

    def find_validator(self, filename):
        from pathlib import PurePath
        validator = self._validators.get(PurePath(filename).suffix.lower())
        if validator is None:
            raise DomainError("Unsupported file extension")
        return validator
