import pytest

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.format_registry import (
    AudioFormatValidator,
    FormatValidatorRegistry,
    ImageFormatValidator,
    MediaCategory,
    PdfFormatValidator,
    TextFormatValidator,
)


def test_pdf_validator_valid_and_invalid_header():
    validator = PdfFormatValidator()
    assert validator.category == MediaCategory.DOCUMENT
    assert ".pdf" in validator.supported_extensions

    # Header hop le
    valid_header = b"%PDF-1.7\r\n..."
    validator.validate(".pdf", "application/pdf", valid_header, file_size=1024)

    # Header khong hop le
    invalid_header = b"NOT_A_PDF_FILE"
    with pytest.raises(DomainError, match="header signature does not match"):
        validator.validate(".pdf", "application/pdf", invalid_header, file_size=1024)


def test_pdf_validator_exceeds_max_size():
    validator = PdfFormatValidator()
    valid_header = b"%PDF-1.4"
    too_large_size = validator.max_size_bytes + 1

    with pytest.raises(DomainError, match="exceeds maximum allowed limit"):
        validator.validate(".pdf", "application/pdf", valid_header, file_size=too_large_size)


def test_image_validator_magic_bytes():
    validator = ImageFormatValidator()
    assert validator.category == MediaCategory.IMAGE

    # PNG
    png_header = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    validator.validate(".png", "image/png", png_header, file_size=2048)

    # JPEG
    jpeg_header = b"\xff\xd8\xff\xe0\x00\x10JFIF"
    validator.validate(".jpg", "image/jpeg", jpeg_header, file_size=2048)

    # WEBP
    webp_header = b"RIFF\x00\x00\x00\x00WEBPVP8 "
    validator.validate(".webp", "image/webp", webp_header, file_size=2048)

    # Invalid
    with pytest.raises(DomainError, match="header signature"):
        validator.validate(".png", "image/png", b"GARBAGE_IMAGE", file_size=2048)


def test_audio_validator_magic_bytes():
    validator = AudioFormatValidator()
    assert validator.category == MediaCategory.AUDIO

    # MP3 ID3
    mp3_id3 = b"ID3\x03\x00\x00\x00\x00\x00"
    validator.validate(".mp3", "audio/mpeg", mp3_id3, file_size=5000)

    # WAV
    wav_header = b"RIFF\x24\x00\x00\x00WAVEfmt "
    validator.validate(".wav", "audio/wav", wav_header, file_size=5000)

    # M4A
    m4a_header = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"
    validator.validate(".m4a", "audio/mp4", m4a_header, file_size=5000)

    # Invalid
    with pytest.raises(DomainError, match="header signature"):
        validator.validate(".mp3", "audio/mpeg", b"NOT_AUDIO", file_size=5000)


def test_text_validator():
    validator = TextFormatValidator()
    assert validator.category == MediaCategory.TEXT

    utf8_header = "Xin chào các bạn học sinh".encode("utf-8")
    validator.validate(".txt", "text/plain", utf8_header, file_size=100)

    invalid_utf8 = b"\xff\xfe\x00\x00\x80\x81"
    with pytest.raises(DomainError, match="header signature"):
        validator.validate(".txt", "text/plain", invalid_utf8, file_size=100)


def test_format_validator_registry():
    registry = FormatValidatorRegistry()

    pdf_val = registry.find_validator(".pdf", "application/pdf")
    assert isinstance(pdf_val, PdfFormatValidator)

    img_val = registry.find_validator(".png", "image/png")
    assert isinstance(img_val, ImageFormatValidator)

    audio_val = registry.find_validator(".mp3", "audio/mpeg")
    assert isinstance(audio_val, AudioFormatValidator)

    text_val = registry.find_validator(".txt", "text/plain")
    assert isinstance(text_val, TextFormatValidator)

    assert registry.is_supported(".pdf", "application/pdf") is True
    assert registry.is_supported(".exe", "application/x-msdownload") is False

    with pytest.raises(DomainError, match="Unsupported file format"):
        registry.find_validator(".exe", "application/x-msdownload")
