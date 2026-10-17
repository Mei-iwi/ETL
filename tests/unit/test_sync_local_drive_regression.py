"""Phase 03 regression: real fixture bytes, mocked Drive/auth, isolated SQLite/files."""

import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from test_multiformat import FORMATS, sample
from test_source_file_import import discover_and_import
from test_source_file_import import importing as importing
from test_source_google_drive import DriveHTTP, assert_source_format_pipeline, reader

from data_sync_etl.adapters.storage.source_google_drive import SCOPE, SourceGoogleDriveFileReader
from data_sync_etl.application.search import SearchService
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import SourceFileError, now
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app


@pytest.mark.parametrize("revocation", ["source_rejected", "source_inactive", "master_inactive"])
def test_retry_of_already_copied_file_rechecks_policy(importing, monkeypatch, revocation):
    c, _, source_path = importing
    process = Mock(side_effect=RuntimeError("isolated queue outage"))
    monkeypatch.setattr(c.source_file_import.process, "run", process)
    item = discover_and_import(c)
    assert item["target_version_id"] and not item["processing_job_id"]
    with c.uow() as repo:
        version = repo.get("resource_versions", item["target_version_id"])
        repo.update(
            "source_file_imports",
            item["id"],
            {
                "next_retry_at": now() - timedelta(seconds=1),
                **(
                    {"source_lifecycle_status": "REJECTED"}
                    if revocation == "source_rejected"
                    else {"source_resource_active": False}
                    if revocation == "source_inactive"
                    else {}
                ),
            },
        )
        if revocation == "master_inactive":
            repo.update("master_learning_resources", item["resource_id"], {"is_active": False})
    c.source_file_import.execute(c.source_file_import.claim())
    assert process.call_count == 1
    with c.uow() as repo:
        current = repo.get("source_file_imports", item["id"])
        assert current["target_version_id"] == version["id"]
        assert repo.count("ocr_jobs", {}) == 0 and repo.count("resource_versions", {}) == 1
        assert current["last_error_code"] == (
            "WAITING_FOR_MASTER" if revocation == "master_inactive" else "BLOCKED_SOURCE_POLICY"
        )
    assert not c.source_file_import.list_files()["items"][0]["retry_eligible"]
    assert (
        c.storage.get_local_path(
            version["storage_bucket"], version["storage_object_key"]
        ).read_bytes()
        == source_path.read_bytes()
    )


def test_revocation_during_copy_does_not_register_version_and_can_recover(importing, monkeypatch):
    c, rows, path = importing
    original = c.storage.save_stream

    def revoke_after_copy(*args, **kwargs):
        result = original(*args, **kwargs)
        with c.uow() as repo:
            item = repo.find("source_file_imports")[0]
            repo.update("source_file_imports", item["id"], {"source_lifecycle_status": "REJECTED"})
        return result

    monkeypatch.setattr(c.storage, "save_stream", revoke_after_copy)
    item = discover_and_import(c)
    assert item["last_error_code"] == "BLOCKED_SOURCE_POLICY"
    with c.uow() as repo:
        assert (
            not item["target_version_id"]
            and repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 0
        )
    # Reapproval is only a fixture SOURCE metadata update, never a fabricated target status.
    with c.uow() as repo:
        c.source_file_import._discover(repo, rows[0])
    monkeypatch.setattr(c.storage, "save_stream", original)
    c.source_file_import.retry(item["id"])
    c.source_file_import.execute(c.source_file_import.claim())
    with c.uow() as repo:
        restored = repo.get("source_file_imports", item["id"])
        assert restored["status"] == "PROCESSING"
        assert repo.count("resource_versions", {}) == repo.count("ocr_jobs", {}) == 1
    assert path.exists()


@pytest.mark.parametrize(
    "content_type", ["text/html", "Text/HTML; charset=utf-8", "application/xhtml+xml", 42]
)
def test_drive_200_html_error_page_is_rejected_without_retry_or_content_leak(content_type):
    http = DriveHTTP(payload=b"<html>PRIVATE_PROVIDER_ERROR</html>")
    original = http.get

    def get(*args, **kwargs):
        response = original(*args, **kwargs)
        if kwargs["params"].get("alt") == "media":
            response.headers = {"Content-Type": content_type}
        return response

    http.get = get
    with pytest.raises(SourceFileError) as error:
        reader(http).open_stream("DRIVE", "files/blob/sample.txt")
    assert error.value.code == "INVALID_DRIVE_RESPONSE" and not error.value.transient
    assert "PRIVATE" not in str(error.value)
    assert all(response.closed for response in http.responses) and http.closed
    assert sum(params.get("alt") == "media" for _, params in http.calls) == 1


@pytest.mark.parametrize("provider", ["local", "google_drive"])
@pytest.mark.parametrize("extension", FORMATS)
def test_file_viewer_reads_destination_bytes_for_supported_sources(
    importing, monkeypatch, extension, provider
):
    payload = sample(extension)
    assert_source_format_pipeline(importing, extension, provider, payload)
    c, _, _ = importing
    c.search = SearchService(c.indexing, c.settings, c.indexing.bags)
    # Explicit policy double for this API transport test; real denial/launch guards
    # remain covered by test_resource_file.py and test_search.py.
    monkeypatch.setattr(
        "data_sync_etl.api.routes.search_access", lambda request: SearchAccess("local_demo")
    )
    with c.uow() as repo:
        version = repo.find("resource_versions")[0]
    # The viewer must not reopen the source after import, even if it goes offline.
    monkeypatch.setattr(
        c.source_file_import.files,
        "open_stream",
        Mock(side_effect=AssertionError("No source proxy")),
    )
    with TestClient(create_app(c)) as api:
        url = f"/api/v1/resource-versions/{version['id']}/file"
        response = api.get(url)
        assert response.status_code == 200 and response.content == payload
        expected_mime = {
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ".txt": "text/plain",
            ".csv": "text/csv",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }
        assert response.headers["content-type"].split(";", 1)[0] == expected_mime[extension]
        assert response.headers["content-disposition"].startswith("inline")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "storage" not in response.headers["content-disposition"]
        downloaded = api.get(url + "?download=true")
        assert downloaded.content == payload and downloaded.headers[
            "content-disposition"
        ].startswith("attachment")
        partial = api.get(url, headers={"Range": "bytes=0-7"})
        assert partial.status_code == 206 and partial.content == payload[:8]


@pytest.mark.parametrize("fault", ["refresh_expired", "requests_timeout"])
def test_oauth_runtime_failures_are_bounded_and_redacted(tmp_path, monkeypatch, fault):
    transport = pytest.importorskip("google.auth.transport.requests")
    credentials = pytest.importorskip("google.oauth2.credentials")
    exceptions = pytest.importorskip("google.auth.exceptions")
    requests_errors = pytest.importorskip("requests.exceptions")
    # Synthetic config only: never open a real token or perform OAuth/network I/O.
    credential_file = tmp_path / "fixture-authorized-user.json"
    credential_file.write_text(json.dumps({"type": "authorized_user"}), encoding="utf-8")
    settings = Settings(
        _env_file=None,
        source_drive_enabled=True,
        source_drive_buckets={"DRIVE": "root"},
        source_drive_auth_type="authorized_user",
        source_drive_credentials_file=credential_file,
        source_drive_max_attempts=3,
    )
    factory = Mock(return_value=object())
    monkeypatch.setattr(credentials.Credentials, "from_authorized_user_info", factory)
    error = (
        exceptions.RefreshError("PRIVATE_PROVIDER_ERROR")
        if fault == "refresh_expired"
        else requests_errors.Timeout("PRIVATE_PROVIDER_ERROR")
    )
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.side_effect = error
    authorized_session = Mock(return_value=session)
    monkeypatch.setattr(transport, "AuthorizedSession", authorized_session)
    drive = SourceGoogleDriveFileReader(settings, sleep=lambda _: None)
    with pytest.raises(SourceFileError) as failure:
        drive.open_stream("DRIVE", "files/blob/sample.txt")
    factory.assert_called_once_with({"type": "authorized_user"}, scopes=[SCOPE])
    assert authorized_session.call_args.kwargs["max_refresh_attempts"] == 1
    assert failure.value.code == (
        "DRIVE_CREDENTIALS_EXPIRED" if fault == "refresh_expired" else "DRIVE_UNAVAILABLE"
    )
    assert failure.value.transient == (fault == "requests_timeout")
    assert session.get.call_count == (1 if fault == "refresh_expired" else 3)
    assert "PRIVATE" not in str(failure.value)
    session.__exit__.assert_called_once()
