import pytest
from sqlalchemy import text

from data_sync_etl.domain.core import DomainError

pytestmark = pytest.mark.postgres


def test_version_reader_requires_explicit_read_only_grant(source):
    with pytest.raises(DomainError, match="BLOCKED_SOURCE_VERSION_ACCESS"):
        source.adapter.list_versions()


def test_version_reader_keyset_and_private_fields(source):
    # Data is confined to the existing isolated test schema, never the real SOURCE.
    with source.engine.begin() as connection:
        connection.execute(text(f'GRANT SELECT ON "{source.schema}".resource_versions TO "{source.role}"'))
        resource_id = connection.scalar(text("SELECT id FROM learning_resources ORDER BY id LIMIT 1"))
        user_id = connection.scalar(text("SELECT id FROM users ORDER BY id LIMIT 1"))
        for number in range(1, 4):
            connection.execute(text(
                "INSERT INTO resource_versions (id,resource_id,version_number,lifecycle_status,"
                "storage_bucket,storage_object_key,source_hash,uploaded_by,created_at,updated_at,parent_version_id) "
                "VALUES (:id,:resource,:number,'UPLOADED','private-bucket','private-object',"
                ":hash,:user,now(),now(),:parent)"
            ), {"id": f"test-version-{number}", "resource": resource_id, "number": number,
                "hash": "a" * 64, "user": user_id, "parent": None if number == 1 else "test-version-1"})
    private = source.adapter.version_page(size=3)["items"]
    assert [v["parent_version_id"] for v in private] == [None, "test-version-1", "test-version-1"]
    assert source.adapter.get_version("test-version-3")["parent_version_id"] == "test-version-1"
    assert source.adapter.get_version("absent") is None
    first = source.adapter.list_versions(size=2)
    second = source.adapter.list_versions(after=first["next_cursor"], size=2)
    assert [row["source_version_id"] for row in first["items"]] == ["test-version-1", "test-version-2"]
    assert [row["source_version_id"] for row in second["items"]] == ["test-version-3"]
    assert second["next_cursor"] is None
    assert "private-" not in str(first)
    with source.engine.begin() as connection:
        connection.execute(text(f'GRANT UPDATE ON "{source.schema}".resource_versions TO "{source.role}"'))
    with pytest.raises(DomainError, match="BLOCKED_SOURCE_VERSION_ACCESS"):
        source.adapter.list_versions()
