from unittest.mock import Mock

import pytest

from data_sync_etl.cli import main as cli
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def test_admin_default_and_startup_warning(container, caplog):
    assert not Settings(_env_file=None, admin_enabled=False).admin_enabled
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        assert client.get("/health").status_code == 200
    assert "without authentication" in caplog.text
    assert "127.0.0.1" in caplog.text


def test_admin_rejects_unknown_body_fields_and_invalid_id(container):
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        response = client.post("/api/v1/master-data/sync", json={"password": "private-value"})
        assert response.status_code == 422 and "private-value" not in response.text
        response = client.get("/api/v1/resource-versions/" + "x" * 31 + "/etl-status")
        assert response.status_code == 422


@pytest.mark.parametrize("failure", [False, True])
def test_cli_exit_code_and_safe_stderr(monkeypatch, capsys, failure):
    monkeypatch.setattr("sys.argv", ["etl", "sync-master", "--full"])
    container = Mock()
    container.close.side_effect = lambda: Container.close(container)
    if failure:
        container.sync.run.side_effect = RuntimeError("private-password-document")
    else:
        container.sync.run.return_value = {"status": "COMPLETED"}
    monkeypatch.setattr(cli, "Container", lambda: container)
    monkeypatch.setattr(cli, "configure", lambda _: None)
    assert cli.main() == (1 if failure else 0)
    captured = capsys.readouterr()
    assert "private-password-document" not in captured.out + captured.err
    assert bool(captured.err) == failure
    container.engine.dispose.assert_called_once()
    container.close.assert_called_once()
    container.source.close.assert_called_once()
    container.mongo_client.close.assert_called_once()


def test_cli_invalid_args_do_not_echo_values(capsys):
    with pytest.raises(SystemExit) as caught:
        cli.parser().parse_args(["sync-master", "--stream", "private-password", "--full"])
    assert caught.value.code == 2
    assert "private-password" not in capsys.readouterr().err
