"""The media migration follows main's soft deletion schema without losing rows."""

import pytest
from sqlalchemy import inspect, text

from tests.conftest import ingest, migrate


def test_media_upgrade_preserves_soft_delete_schema_and_guards_rollback(container):
    version = ingest(container)
    migrate(container.engine, "0017", downgrade=True)
    schema = inspect(container.engine)
    assert "resource_deletion_events" in schema.get_table_names()
    assert "processing_options" not in {c["name"] for c in schema.get_columns("resource_versions")}
    assert "source_locator" not in {c["name"] for c in schema.get_columns("content_units")}
    with container.engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM resource_versions WHERE id=:id"),
                                 {"id": version["id"]}) == 1
    migrate(container.engine)
    with container.uow() as repo:
        assert repo.get("resource_versions", version["id"])["processing_options"] is None
        repo.update("resource_versions", version["id"], {"processing_options": {"source_language": "vi"}})
    with pytest.raises(RuntimeError, match="0018 processing data exists"):
        migrate(container.engine, "0017", downgrade=True)
    with container.uow() as repo:
        assert repo.get("resource_versions", version["id"])["processing_options"] == {"source_language": "vi"}
    assert "resource_deletion_events" in inspect(container.engine).get_table_names()
