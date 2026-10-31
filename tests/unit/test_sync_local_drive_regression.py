"""Phase 03 regression: real fixture bytes, mocked Drive/auth, isolated SQLite/files."""

import json
from unittest.mock import Mock

import pytest
from test_multiformat import FORMATS, sample
from test_source_file_import import discover_and_import
from test_source_file_import import importing as importing
from test_source_google_drive import DriveHTTP, reader

from data_sync_etl.adapters.storage.source_google_drive import SCOPE, SourceGoogleDriveFileReader
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import SourceFileError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


@pytest.mark.parametrize("revocation", ["source_rejected", "source_inactive", "master_inactive"])
def test_metadata_only_file_never_enters_retry_or_content_pipeline(importing, monkeypatch, revocation):
    c, rows, source_path = importing
    forbidden = Mock(side_effect=AssertionError("SOURCE bytes are metadata-only"))
    monkeypatch.setattr(c.source_files, "open_stream", forbidden)
    process = Mock(side_effect=AssertionError("SOURCE metadata cannot queue OCR"))
    monkeypatch.setattr(c.source_file_import.process, "run", process)
    item = discover_and_import(c)
    assert item["status"] == "METADATA_ONLY" and item["target_version_id"]
    with c.uow() as repo:
        if revocation == "source_rejected":
            rows[0]["source_lifecycle_status"] = "REJECTED"
        elif revocation == "source_inactive":
            rows[0]["resource_active"] = False
        else:
            repo.update("master_learning_resources", item["resource_id"], {"is_active": False})
    if revocation != "master_inactive":
        c.source_file_import.discover_once()
        with pytest.raises(SourceFileError, match="BLOCKED_SOURCE_POLICY"):
            c.source_resolver.resolve(item["target_version_id"])
    else:
        with pytest.raises(SourceFileError, match="FILE_NOT_FOUND"):
            c.source_resolver.resolve(item["target_version_id"])
    assert c.source_file_import.claim() is None
    assert not c.source_file_import.list_files()["items"][0]["retry_eligible"]
    process.assert_not_called()
    forbidden.assert_not_called()
    assert source_path.exists()
    assert not list((c.storage.root / 'resources').rglob('*'))


def test_revocation_during_metadata_discovery_never_copies_or_recovers_content(importing, monkeypatch):
    c, rows, path = importing
    forbidden = Mock(side_effect=AssertionError("SOURCE bytes must not be opened"))
    monkeypatch.setattr(c.source_files, "open_stream", forbidden)
    item = discover_and_import(c)
    assert item["status"] == "METADATA_ONLY"
    with c.uow() as repo:
        restored = repo.get("source_file_imports", item["id"])
        assert restored["status"] == "METADATA_ONLY"
        assert repo.count("resource_versions", {}) == 1
        assert repo.count("ocr_jobs", {}) == 0
    assert c.source_file_import.claim() is None
    with pytest.raises(Exception, match="EXTERNAL_SOURCE_CONTENT_DISABLED"):
        c.source_file_import.retry(item["id"])
    forbidden.assert_not_called()
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


@pytest.mark.parametrize("extension", FORMATS)
def test_legacy_source_file_viewer_is_metadata_only_for_supported_formats(importing, monkeypatch, extension):
    payload = sample(extension)
    c, rows, source_path = importing
    source_path.write_bytes(payload)
    rows[0]["storage_object_key"] = f"test{extension}"
    from hashlib import sha256
    rows[0]["source_hash"] = sha256(payload).hexdigest()
    item = discover_and_import(c)
    with c.uow() as repo:
        version = repo.get("resource_versions", item["target_version_id"])
    c.settings.source_reference_enabled = True
    c.search.file_version = lambda version_id, access=None, **kwargs: version
    no_source_io = Mock(side_effect=AssertionError("No SOURCE byte access"))
    monkeypatch.setattr(c.source_files, "open_stream", no_source_io)
    monkeypatch.setattr(c.storage, "get_local_path", no_source_io)
    monkeypatch.setattr(c.storage, "exists", no_source_io)
    monkeypatch.setattr(
        "data_sync_etl.api.routes.search_access", lambda request: SearchAccess("local_demo")
    )
    with TestClient(create_app(c)) as api:
        url = f"/api/v1/resource-versions/{version['id']}/file"
        for suffix in ("", "?download=true", "?view=preview", "?view=text"):
            response = api.get(url + suffix)
            assert response.status_code == 409, response.text
            expected_code = "FILE_NOT_READY" if suffix == "?view=text" else "SOURCE_OPEN_UNSUPPORTED"
            assert response.json()["detail"]["code"] == expected_code
    no_source_io.assert_not_called()
    assert not list(c.settings.source_temp_root.glob('attempt-*'))


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
