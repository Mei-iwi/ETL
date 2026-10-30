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
