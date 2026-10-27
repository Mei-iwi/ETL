import importlib
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import ProgrammingError


@pytest.mark.parametrize("sqlstate", ["42703", "42P01"])
def test_cli_schema_error_is_actionable_and_hides_sensitive_details(sqlstate, monkeypatch, capsys):
    cli = importlib.import_module("data_sync_etl.cli.main")
    error = Exception("sensitive database details")
    error.sqlstate = sqlstate
    closed = []
    container = SimpleNamespace(settings=SimpleNamespace(log_level="INFO"),
                                close=lambda: closed.append(True))
    monkeypatch.setattr(cli, "Container", lambda: container)
    monkeypatch.setattr(cli.sys, "argv", ["etl", "run-ocr-worker", "--worker-id", "test", "--once"])

    def fail(*_):
        raise ProgrammingError("sensitive SQL", {}, error)

    monkeypatch.setattr(cli, "dispatch", fail)
    assert cli.main() == 1
    output = capsys.readouterr().err
    assert "MASTER_SCHEMA_MIGRATION_REQUIRED" in output
    assert "python -m alembic upgrade head" in output
    assert "sensitive" not in output
    assert closed == [True]
