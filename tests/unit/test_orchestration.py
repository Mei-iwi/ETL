from unittest.mock import Mock

import pytest
from conftest import make_pdf

from data_sync_etl.cli.main import demo_run
from data_sync_etl.domain.core import DomainError


def test_start_requires_existing_master_without_sync(container, monkeypatch):
    sync = Mock(side_effect=AssertionError("Implicit sync is forbidden"))
    monkeypatch.setattr(container.sync, "run", sync)
    with pytest.raises(DomainError, match="Resource not found"):
        container.orchestrator.start("resource-1", "input", "sample.pdf")
    sync.assert_not_called()


def test_explicit_convenience_mode_is_incremental(container, monkeypatch):
    make_pdf(container)
    real = container.sync.run
    sync = Mock(wraps=real)
    monkeypatch.setattr(container.sync, "run", sync)
    result = container.orchestrator.start("resource-1", "input", "sample.pdf", sync_master=True)
    assert result["action"] == "CREATED"
    sync.assert_called_once_with(full=False)


def test_demo_bootstraps_once_then_no_change(container, monkeypatch):
    real = container.sync.run
    sync = Mock(wraps=real)
    monkeypatch.setattr(container.sync, "run", sync)
    assert demo_run(container, "resource-1")["content_unit_count"] == 3
    assert demo_run(container, "resource-1")["action"] == "NO_CHANGE"
    sync.assert_called_once_with(full=True)
