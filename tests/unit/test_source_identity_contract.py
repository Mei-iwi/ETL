import unicodedata

import pytest

from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import (
    SourceIdentity,
    SourceLocator,
    source_identity,
    source_identity_hash,
)


def test_source_identity_is_deterministic_and_nfc_normalized():
    composed = SourceLocator(
        backend="PostgreSQL",
        namespace="tenant-a",
        entity_type="resources",
        source_record_id="é-1",
    )
    decomposed = SourceLocator(
        backend="postgresql",
        namespace="tenant-a",
        entity_type="RESOURCES",
        source_record_id=unicodedata.normalize("NFD", "é-1"),
    )
    assert source_identity_hash(composed) == source_identity_hash(decomposed)
    identity = source_identity(composed)
    assert identity.digest == source_identity_hash(composed)
    assert "postgresql" not in identity.digest  # digest is not a reversible locator


@pytest.mark.parametrize(
    "field,value",
    [
        ("namespace", ""),
        ("source_record_id", " "),
        ("entity_type", "x\n"),
        ("source_record_id", "x" * 256),
        ("namespace", "postgresql://user:secret@host/db"),
    ],
)
def test_source_identity_rejects_empty_control_or_oversized_parts(field, value):
    values = dict(
        backend="postgresql",
        namespace="tenant-a",
        entity_type="resources",
        source_record_id="resource-1",
    )
    values[field] = value
    with pytest.raises(ValueError):
        SourceLocator(**values)


def test_source_identity_rejects_a_digest_that_does_not_match_the_locator():
    locator = SourceLocator(
        backend="postgresql",
        namespace="tenant-a",
        entity_type="resources",
        source_record_id="resource-1",
    )
    with pytest.raises(ValueError):
        SourceIdentity(locator=locator, digest="0" * 64)


@pytest.mark.parametrize('part', ['backend', 'namespace', 'entity_type', 'source_record_id'])
def test_same_record_id_is_distinct_across_every_identity_component(part):
    first = SourceLocator(backend='postgresql', namespace='source-a',
                          entity_type='resources', source_record_id='same-id')
    second = SourceLocator(**{**first.model_dump(), part: 'different-value'})
    assert source_identity_hash(first) != source_identity_hash(second)


def test_postgresql_namespace_is_server_configurable_without_connection():
    from types import SimpleNamespace

    from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    a = PostgreSQLSourceAdapter(engine=engine, namespace='source-a')
    b = PostgreSQLSourceAdapter(engine=engine, namespace='source-b')
    assert source_identity_hash(a.locator('resources', 'same-id')) != source_identity_hash(b.locator('resources', 'same-id'))


def test_readonly_reader_rejects_write_privileges_before_yielding():
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
    engine, connection = MagicMock(), MagicMock()
    engine.dialect = SimpleNamespace(name='postgresql')
    engine.connect.return_value.execution_options.return_value.__enter__.return_value = connection
    connection.execute.return_value.scalar_one.return_value = False
    connection.scalar.return_value = True  # Trusted catalog reports a write grant.
    adapter = PostgreSQLSourceAdapter(engine=engine)
    with pytest.raises(DomainError, match='write privileges'):
        with adapter.snapshot():
            pytest.fail('Writer credentials reached the read-only data reader')
    engine.connect.return_value.execution_options.assert_called_once_with(
        isolation_level='REPEATABLE READ', postgresql_readonly=True)
    assert not hasattr(adapter, 'write_metadata')


def test_source_sync_does_not_merge_same_id_from_another_namespace(container):
    container.source.source_origin = "POSTGRESQL"
    container.sync.run(full=True)
    with container.uow() as repo:
        row = repo.get("master_learning_resources", "resource-1")
        assert row["source_identity_hash"]
        assert row["source_namespace"] == "fixture:source"
        foreign = SourceLocator(
            backend="postgresql",
            namespace="another-source",
            entity_type="resources",
            source_record_id="resource-1",
        )
        with pytest.raises(DomainError, match="BLOCKED_SOURCE_IDENTITY"):
            repo.sync_upsert(
                "master_learning_resources",
                {"id": "resource-1"},
                source_origin="POSTGRESQL",
                source_locator=foreign,
            )


def test_same_source_id_recreated_record_is_not_adopted(container):
    from data_sync_etl.domain.core import now
    container.source.source_origin = 'POSTGRESQL'
    container.sync.run(full=True)
    with pytest.raises(DomainError, match='BLOCKED_SOURCE_IDENTITY'), container.uow() as repo:
        row = repo.get('master_learning_resources', 'resource-1')
        repo.sync_upsert('master_learning_resources', {**row, 'created_at': now()},
                         source_origin='POSTGRESQL',
                         source_locator=container.source.locator('resources', 'resource-1'))
