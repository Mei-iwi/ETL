from unittest.mock import Mock

import pytest

from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.composition import create_source
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError


def test_json_does_not_connect_source(monkeypatch):
    connect = Mock(side_effect=AssertionError("JSON must not create source engine"))
    monkeypatch.setattr("data_sync_etl.adapters.source.postgresql.create_engine", connect)
    settings = Settings(_env_file=None, source_adapter="json", source_database_url="not-a-url")
    assert isinstance(create_source(settings), JsonFixtureSourceAdapter)
    connect.assert_not_called()


@pytest.mark.parametrize("adapter", ["postgresql", "production"])
def test_postgresql_requires_explicit_source_url(adapter):
    with pytest.raises(DomainError, match="SOURCE_DATABASE_URL required"):
        create_source(Settings(_env_file=None, source_adapter=adapter, source_database_url=None))


def test_source_urls_are_not_in_settings_repr():
    settings = Settings(
        _env_file=None,
        source_database_url="private-reader-url",
        source_migration_database_url="private-owner-url",
    )
    assert "private-reader-url" not in repr(settings)
    assert "private-owner-url" not in repr(settings)
