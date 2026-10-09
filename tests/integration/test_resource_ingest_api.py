import io

from fastapi.testclient import TestClient

from data_sync_etl.main import create_app


def test_post_resource_pdf_multipart_success(container):
    container.sync.run(full=True)
    pdf_bytes = b"%PDF-1.7 \n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<<>>\n%%EOF"

    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "Đề thi thử THPT Quốc Gia 2026",
                "resource_type_code": "EXAM",
                "provider_id": "prov-so-gd",
                "provider_name": "Sở GD&ĐT TP.HCM",
                "provider_type": "INTERNAL",
                "subject_id": "subject-1",
                "grade_level_id": "grade-1",
                "publication_status": "PUBLISHED",
            },
            files={
                "file": ("de_thi_toan.pdf", io.BytesIO(pdf_bytes), "application/pdf"),
            },
        )

    assert response.status_code == 201
    data = response.json()

    assert data["id"]
    assert data["title"] == "Đề thi thử THPT Quốc Gia 2026"
    assert data["resource_type_code"] == "EXAM"
    assert data["media_category"] == "DOCUMENT"
    assert data["mime_type"] == "application/pdf"
    assert data["file_name"] == "de_thi_toan.pdf"
    assert data["file_size"] == len(pdf_bytes)
    assert len(data["source_hash"]) == 64
    assert data["initial_version_id"]
    assert data["version_number"] == 1
    assert data["lifecycle_status"] == "INGESTED"

    # Xac thuc file co tren disk storage
    assert container.storage.exists("_versions", f"objects/{data['source_hash']}")


def test_post_resource_image_png_success(container):
    png_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 40

    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "Bản đồ Địa lý Tự nhiên",
                "resource_type_code": "MAP",
                "provider_id": "prov-geo-01",
                "provider_name": "Ban Địa lý",
            },
            files={
                "file": ("vietnam_map.png", io.BytesIO(png_bytes), "image/png"),
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["media_category"] == "IMAGE"
    assert data["mime_type"] == "image/png"
    assert data["file_name"] == "vietnam_map.png"


def test_post_resource_audio_mp3_success(container):
    mp3_bytes = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\x00" * 150

    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "Hội thoại Tiếng Pháp A1",
                "resource_type_code": "AUDIO_LESSON",
                "provider_id": "prov-lang-01",
                "provider_name": "Viện Trao đổi Văn hóa",
            },
            files={
                "file": ("conversation_a1.mp3", io.BytesIO(mp3_bytes), "audio/mpeg"),
            },
        )

    assert response.status_code == 201
    data = response.json()
    assert data["media_category"] == "AUDIO"
    assert data["mime_type"] == "audio/mpeg"


def test_post_resource_missing_required_fields(container):
    pdf_bytes = b"%PDF-1.4 sample content"

    with TestClient(create_app(container)) as client:
        # Thieu title va provider_id
        response = client.post(
            "/api/v1/resources",
            data={
                "resource_type_code": "EXAM",
            },
            files={
                "file": ("file.pdf", io.BytesIO(pdf_bytes), "application/pdf"),
            },
        )

    assert response.status_code == 422


def test_post_resource_corrupted_header_fails(container):
    invalid_content = b"THIS_IS_NOT_A_VALID_PDF_AT_ALL"

    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "Tài liệu hỏng",
                "resource_type_code": "HANDOUT",
                "provider_id": "prov-01",
                "provider_name": "NXB",
            },
            files={
                "file": ("corrupted.pdf", io.BytesIO(invalid_content), "application/pdf"),
            },
        )

    assert response.status_code == 400
    assert "Invalid ETL operation" in response.json()["detail"]


def test_post_resource_unsupported_format_fails(container):
    exe_content = b"\x4d\x5a\x90\x00"

    with TestClient(create_app(container)) as client:
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "Phần mềm độc hại",
                "resource_type_code": "SOFTWARE",
                "provider_id": "prov-01",
                "provider_name": "Unknown",
            },
            files={
                "file": ("virus.exe", io.BytesIO(exe_content), "application/octet-stream"),
            },
        )

    assert response.status_code == 400
