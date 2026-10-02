import importlib.util
from pathlib import Path
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from data_sync_etl.domain.core import DomainError
from data_sync_etl.logging import event
from data_sync_etl.main import create_app
from data_sync_etl.ports.processing import OCRResult
from data_sync_etl.ports.source import User


@pytest.mark.parametrize("exception", [RuntimeError, DomainError])
def test_api_consumes_sensitive_errors(container, monkeypatch, caplog, exception):
    container.settings.admin_enabled = True

    def fail(*args, **kwargs):
        raise exception("private-password-and-document-body")

    monkeypatch.setattr(container.sync, "run", fail)
    # Default raise_server_exceptions=True also proves no traceback escapes to server logs.
    with TestClient(create_app(container)) as client:
        response = client.post("/admin/sync/master", json={"full": True})
    assert response.status_code == (400 if exception is DomainError else 500)
    assert "private-password-and-document-body" not in response.text + caplog.text


def test_result_repr_and_log_allowlist(caplog):
    result = OCRResult("private-document", metadata={"token": "private-token"})
    assert "private-" not in repr(result)
    with caplog.at_level("INFO"):
        event(action="TEST", password="private-password", raw_text="private-document")
    assert "private-" not in caplog.text


def test_source_validation_hides_input():
    with pytest.raises(ValidationError) as caught:
        User.model_validate({"hashed_password": "private-password", "id": "private-" * 10})
    assert "private-" not in str(caught.value)


def test_handoff_excludes_local_artifacts(tmp_path):
    path = Path(__file__).parents[2] / "scripts/package_source.py"
    spec = importlib.util.spec_from_file_location("package_source", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in [
        ".env",
        ".env.local",
        "storage/demo/a.pdf",
        "src/demo.egg-info/x.py",
        "src/__pycache__/bad.py",
        ".test-tmp/test.db",
    ]:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("private-local-data")
    (tmp_path / ".env.example").write_text("PLACEHOLDER")
    (tmp_path / "src/app.py").write_text("print('example')")
    output = tmp_path / "delivery.zip"
    module.package(tmp_path, output)
    with ZipFile(output) as archive:
        assert set(archive.namelist()) == {".env.example", "src/app.py"}
        assert all(b"private-local-data" not in archive.read(p) for p in archive.namelist())
