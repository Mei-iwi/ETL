"""The media migration follows main's soft deletion schema without losing rows."""

import pytest
from sqlalchemy import inspect, text

from tests.conftest import ingest, migrate


def legacy_media_database(container):
    version = ingest(container)
    migrate(container.engine, "0016", downgrade=True)
    with container.engine.begin() as connection:
        connection.execute(text("ALTER TABLE resource_versions ADD COLUMN processing_options JSON"))
        connection.execute(text("ALTER TABLE content_units ADD COLUMN source_locator JSON"))
        connection.execute(text("UPDATE alembic_version SET version_num='0017'"))
        connection.execute(text("UPDATE resource_versions SET processing_options=:value WHERE id=:id"),
                           {"value": '{"source_language":"vi"}', "id": version["id"]})
    return version


def test_legacy_0017_media_upgrade_adds_missing_soft_delete_without_losing_data(container):
    version = legacy_media_database(container)
    migrate(container.engine)
    with container.uow() as repo:
        assert repo.get("resource_versions", version["id"])["processing_options"] == {"source_language": "vi"}
        resource = repo.get("master_learning_resources", version["resource_id"])
        assert resource["deletion_revision"] == 0
        assert resource["master_deleted_at"] is None
    schema = inspect(container.engine)
    assert "resource_deletion_events" in schema.get_table_names()
    assert "source_locator" in {c["name"] for c in schema.get_columns("content_units")}
    with container.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0018"
    migrate(container.engine)  # A repeated upgrade must be a no-op.


def test_legacy_partial_schema_requires_review(container):
    legacy_media_database(container)
    with container.engine.begin() as connection:
        connection.execute(text("ALTER TABLE master_learning_resources ADD COLUMN deletion_revision INTEGER"))
    with pytest.raises(RuntimeError, match="0018_LEGACY_SCHEMA_REQUIRES_MANUAL_REVIEW"):
        migrate(container.engine)
    with container.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0017"
    assert "resource_deletion_events" not in inspect(container.engine).get_table_names()


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
