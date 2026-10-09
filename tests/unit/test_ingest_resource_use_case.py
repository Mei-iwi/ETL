import io

import pytest

from data_sync_etl.application.ingest_resource import IngestResourceCommand
from data_sync_etl.domain.core import DomainError


def test_ingest_resource_pdf_success(container):
    container.sync.run(full=True)
    pdf_content = b"%PDF-1.5 \n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    stream = io.BytesIO(pdf_content)

    cmd = IngestResourceCommand(
        title="Tài liệu Giải tích 12",
        resource_type_code="HANDOUT",
        provider_id="provider-internal-01",
        provider_name="Nhà xuất bản Giáo dục",
        filename="giai_tich_12.pdf",
        file_stream=stream,
        file_size=len(pdf_content),
        declared_mime="application/pdf",
        subject_id="subject-1",
        grade_level_id="grade-1",
        uploaded_by="user-admin-01",
    )

    result = container.resource_ingest.execute(cmd)

    assert result.id
    assert result.title == "Tài liệu Giải tích 12"
    assert result.resource_type_code == "HANDOUT"
    assert result.media_category == "DOCUMENT"
    assert result.mime_type == "application/pdf"
    assert result.file_name == "giai_tich_12.pdf"
    assert result.file_size == len(pdf_content)
    assert len(result.source_hash) == 64
    assert result.version_number == 1
    assert result.lifecycle_status == "INGESTED"

    # Kiem tra trong DB
    with container.uow() as repo:
        resource = repo.get("master_learning_resources", result.id)
        assert resource is not None
        assert resource["title"] == "Tài liệu Giải tích 12"
        assert resource["provider_name"] == "Nhà xuất bản Giáo dục"
        assert resource["is_active"] is True

        version = repo.get("resource_versions", result.initial_version_id)
        assert version is not None
        assert version["resource_id"] == result.id
        assert version["version_number"] == 1
        assert version["source_hash"] == result.source_hash

        subjects = repo.find("resource_subjects", {"resource_id": result.id})
        assert len(subjects) == 1
        assert subjects[0]["subject_id"] == "subject-1"

        grades = repo.find("resource_grade_levels", {"resource_id": result.id})
        assert len(grades) == 1
        assert grades[0]["grade_level_id"] == "grade-1"

    # Kiem tra file vat ly da duoc luu trong storage
    assert container.storage.exists("_versions", f"objects/{result.source_hash}")


def test_ingest_resource_audio_success(container):
    mp3_content = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 200
    stream = io.BytesIO(mp3_content)

    cmd = IngestResourceCommand(
        title="Bài nghe Tiếng Anh Unit 1",
        resource_type_code="AUDIO_LESSON",
        provider_id="provider-english-01",
        provider_name="Ban Đề án Ngoại ngữ",
        filename="unit_1_listening.mp3",
        file_stream=stream,
        file_size=len(mp3_content),
        declared_mime="audio/mpeg",
    )

    result = container.resource_ingest.execute(cmd)

    assert result.media_category == "AUDIO"
    assert result.mime_type == "audio/mpeg"
    assert container.storage.exists("_versions", f"objects/{result.source_hash}")


def test_ingest_resource_image_success(container):
    png_content = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 50
    stream = io.BytesIO(png_content)

    cmd = IngestResourceCommand(
        title="Sơ đồ tư duy Hóa học",
        resource_type_code="DIAGRAM",
        provider_id="provider-chem-01",
        provider_name="Tổ Hóa",
        filename="mindmap_hoa_hoc.png",
        file_stream=stream,
        file_size=len(png_content),
        declared_mime="image/png",
    )

    result = container.resource_ingest.execute(cmd)
    assert result.media_category == "IMAGE"
    assert result.mime_type == "image/png"


def test_ingest_resource_fails_on_corrupted_header(container):
    corrupted_pdf = b"THIS_IS_NOT_A_VALID_PDF_CONTENT"
    stream = io.BytesIO(corrupted_pdf)

    cmd = IngestResourceCommand(
        title="File rác",
        resource_type_code="HANDOUT",
        provider_id="provider-01",
        provider_name="Anonymous",
        filename="fake.pdf",
        file_stream=stream,
        file_size=len(corrupted_pdf),
        declared_mime="application/pdf",
    )

    with pytest.raises(DomainError, match="header signature does not match"):
        container.resource_ingest.execute(cmd)


def test_ingest_resource_fails_on_unsupported_format(container):
    binary_content = b"\x4d\x5a\x90\x00"
    stream = io.BytesIO(binary_content)

    cmd = IngestResourceCommand(
        title="Tệp thực thi không cho phép",
        resource_type_code="HANDOUT",
        provider_id="provider-01",
        provider_name="Anonymous",
        filename="dangerous.exe",
        file_stream=stream,
        file_size=len(binary_content),
        declared_mime="application/x-msdownload",
    )

    with pytest.raises(DomainError, match="Unsupported file format"):
        container.resource_ingest.execute(cmd)
