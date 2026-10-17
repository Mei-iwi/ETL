import asyncio
import io

import pymupdf
import pytest

from data_sync_etl.main import UploadBodyLimitMiddleware, create_app
from tests.local_admin_client import TestClient


def pdf_bytes():
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Real PDF for upload")
        return pdf.tobytes()


def post_resource(
    container,
    payload=None,
    *,
    filename="sample.pdf",
    mime="application/pdf",
    data=None,
    client_host="127.0.0.1",
):
    values = {
        "title": "New learning resource",
        "resource_type_code": "HANDOUT",
        "provider_id": "provider-test",
        "provider_name": "Test provider",
        **(data or {}),
    }
    with TestClient(create_app(container), client=(client_host, 50000)) as client:
        return client.post(
            "/api/v1/resources",
            data=values,
            files={
                "file": (filename, io.BytesIO(pdf_bytes() if payload is None else payload), mime)
            },
        )


def test_create_resource_creates_initial_version(container):
    container.settings.admin_enabled = True
    response = post_resource(container)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "New learning resource"
    assert body["version_number"] == 1
    assert body["lifecycle_status"] == "INGESTED"
    assert len(body["source_hash"]) == 64
    with container.uow() as repo:
        resource = repo.get("master_learning_resources", body["id"])
        version = repo.get("resource_versions", body["initial_version_id"])
    assert resource["title"] == body["title"]
    assert version["resource_id"] == body["id"]
    assert version["source_hash"] == body["source_hash"]
    assert container.storage.exists(version["storage_bucket"], version["storage_object_key"])
    with container.uow() as repo:
        assert repo.count("ocr_jobs", {}) == 0


def test_create_resource_rejects_mismatched_signature(container):
    container.settings.admin_enabled = True
    response = post_resource(container, b"not a pdf", data={"title": "Invalid resource"})
    assert response.status_code == 400
    assert "not a pdf" not in response.text
    with container.uow() as repo:
        assert repo.count("master_learning_resources", {"title": "Invalid resource"}) == 0


def test_create_resource_rejects_unauthorized_and_nonlocal(container):
    assert post_resource(container).status_code == 403
    with TestClient(create_app(container), client=("127.0.0.1", 50000)) as client:
        assert client.post("/api/v1/resources").status_code == 403
    container.settings.admin_enabled = True
    assert post_resource(container, client_host="203.0.113.10").status_code == 403
    with container.uow() as repo:
        assert repo.count("master_learning_resources", {"title": "New learning resource"}) == 0


def test_create_resource_rejects_fake_pdf_and_claimed_identity(container):
    container.settings.admin_enabled = True
    fake = post_resource(container, b"%PDF-1.7\nnot a real PDF")
    assert fake.status_code == 400, fake.text
    assert fake.json()["detail"]["code"] == "INVALID_FILE"
    assert list((container.storage.root / "resources").glob(".upload-*")) == []
    assert post_resource(container, data={"uploaded_by": "admin"}).status_code == 400


def test_create_resource_rejects_encrypted_pdf(container):
    container.settings.admin_enabled = True
    with pymupdf.open() as pdf:
        pdf.new_page()
        encrypted = pdf.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="secret"
        )
    response = post_resource(container, encrypted)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_FILE"


def test_create_resource_validates_subject_grade_pair(container):
    container.settings.admin_enabled = True
    container.sync.run("grades", full=True)
    container.sync.run("subjects", full=True)
    ok = post_resource(container, data={"subject_id": "subject-1", "grade_level_id": "grade-1"})
    assert ok.status_code == 201, ok.text
    bad = post_resource(container, data={"subject_id": "subject-1", "grade_level_id": "grade-2"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "INVALID_METADATA"


@pytest.mark.parametrize(
    "field,value", [("subject_id", "missing-subject"), ("grade_level_id", "missing-grade")]
)
def test_create_resource_rejects_unknown_relation_ids(container, field, value):
    container.settings.admin_enabled = True
    response = post_resource(container, data={field: value})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_METADATA"


def test_create_resource_rejects_known_provider_conflict(container):
    container.settings.admin_enabled = True
    container.sync.run("all", full=True)
    bad = post_resource(
        container,
        data={
            "provider_id": "provider-1",
            "provider_name": "Impersonator",
            "provider_type": "SCHOOL",
        },
    )
    assert bad.status_code == 400
    assert bad.json()["detail"]["code"] == "INVALID_METADATA"


def test_create_resource_reuses_known_resource_type_metadata(container):
    container.settings.admin_enabled = True
    container.sync.run("all", full=True)
    response = post_resource(
        container,
        data={
            "provider_id": "provider-1",
            "provider_name": "Demo School",
            "provider_type": "SCHOOL",
            "resource_type_code": "TEXTBOOK",
            "subject_id": "subject-1",
            "grade_level_id": "grade-1",
        },
    )
    assert response.status_code == 201, response.text
    with container.uow() as repo:
        resource = repo.get("master_learning_resources", response.json()["id"])
    assert resource["resource_type_id"] == "type-1"
    assert resource["resource_type_name_vi"] == "Sach giao khoa"
    assert resource["publication_status"] == "PUBLISHED"


def test_create_resource_request_validation(container):
    container.settings.admin_enabled = True
    with TestClient(create_app(container), client=("127.0.0.1", 50000)) as client:
        missing_file = client.post("/api/v1/resources", data={"title": "No file"})
        missing_title = client.post(
            "/api/v1/resources",
            data={"resource_type_code": "HANDOUT", "provider_id": "p", "provider_name": "P"},
            files={"file": ("x.pdf", io.BytesIO(pdf_bytes()), "application/pdf")},
        )
    assert missing_file.status_code == 422
    assert missing_title.status_code == 422


def test_create_resource_rejects_wrong_mime_extension_and_empty_file(container):
    container.settings.admin_enabled = True
    assert post_resource(container, mime="text/plain").status_code == 400
    assert post_resource(container, filename="sample.txt").status_code == 400
    assert post_resource(container, b"").status_code == 400


def test_create_resource_rejects_oversized_file(container, monkeypatch):
    from data_sync_etl.domain.format_validation import PdfFormatValidator

    container.settings.admin_enabled = True
    monkeypatch.setattr(PdfFormatValidator, "max_size_bytes", 10)
    response = post_resource(container)
    assert response.status_code == 413


def test_create_resource_rejects_oversized_request_before_parsing(container):
    container.settings.admin_enabled = True
    container.settings.upload_request_max_bytes = 1024
    response = post_resource(container)
    assert response.status_code == 413, response.text


def test_upload_body_limit_rejects_stream_without_content_length():
    async def check():
        chunks = iter(
            [
                {"type": "http.request", "body": b"abc", "more_body": True},
                {"type": "http.request", "body": b"def", "more_body": False},
            ]
        )
        sent = []

        async def receive():
            return next(chunks)

        async def send(message):
            sent.append(message)

        async def app(_scope, receive_body, send_response):
            while True:
                message = await receive_body()
                if not message.get("more_body"):
                    break
            await send_response({"type": "http.response.start", "status": 200, "headers": []})

        middleware = UploadBodyLimitMiddleware(app, max_bytes=4)
        await middleware(
            {"type": "http", "method": "POST", "path": "/api/v1/resources", "headers": []},
            receive,
            send,
        )
        return sent

    sent = asyncio.run(check())
    assert sent[0]["status"] == 413


def test_create_resource_rejects_chunked_oversized_request(container):
    container.settings.admin_enabled = True
    container.settings.upload_request_max_bytes = 1024
    with TestClient(create_app(container), client=("127.0.0.1", 50000)) as client:
        response = client.post(
            "/api/v1/resources",
            headers={"Content-Type": "multipart/form-data; boundary=demo"},
            content=(
                chunk
                for chunk in [
                    b'--demo\r\nContent-Disposition: form-data; name="file"; filename="x.pdf"\r\n'
                    b"Content-Type: application/pdf\r\n\r\n",
                    b"a" * 700,
                    b"b" * 700,
                    b"\r\n--demo--\r\n",
                ]
            ),
        )
    assert response.status_code == 413, response.text


def test_create_resource_preserves_object_after_database_failure(container, monkeypatch):
    container.settings.admin_enabled = True
    original = container.uow

    class FailingUow:
        def __init__(self):
            self.calls = 0

        def __call__(self):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("controlled database failure")
            return original()

    monkeypatch.setattr(container.resource_create, "uow", FailingUow())
    payload = pdf_bytes()
    response = post_resource(container, payload)
    assert response.status_code == 503
    from hashlib import sha256

    digest = sha256(payload).hexdigest()
    assert container.storage.exists("resources", f"{digest[:2]}/{digest}.pdf")


def test_create_resource_reports_database_lookup_failure_as_503(container, monkeypatch):
    container.settings.admin_enabled = True

    def unavailable():
        raise OSError("controlled database outage")

    monkeypatch.setattr(container.resource_create, "uow", unavailable)
    response = post_resource(container)
    assert response.status_code == 503
    assert response.json()["detail"]["phase"] == "persistence"
    assert list((container.storage.root / "resources").glob(".upload-*")) == []
