import json

import pytest
from pydantic import ValidationError

from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.composition import create_source
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError


def test_source_selection():
    assert isinstance(
        create_source(Settings(_env_file=None, source_adapter="json")), JsonFixtureSourceAdapter
    )
    with pytest.raises(DomainError, match="BLOCKED_SOURCE_MAPPING"):
        create_source(Settings(_env_file=None, source_adapter="production"))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, source_adapter="unknown")


@pytest.mark.parametrize("later", [False, True])
def test_duplicates_across_single_record_batches(container, later):
    path = container.source.root / "resources.json"
    rows = json.loads(path.read_text())
    duplicate = dict(rows[0])
    if later:
        duplicate["updated_at"] = "2026-12-01T00:00:00Z"
    rows.append(duplicate)
    path.write_text(json.dumps(rows))
    with pytest.raises(DomainError, match="Duplicate"):
        next(container.source.batches("resources", None, 1))


@pytest.mark.parametrize("file", ["providers", "resource_subjects", "resource_grades"])
def test_duplicate_dependency_or_relation(container, file):
    path = container.source.root / f"{file}.json"
    rows = json.loads(path.read_text())
    rows.append(rows[0])
    path.write_text(json.dumps(rows))
    with pytest.raises(DomainError, match="Duplicate"):
        next(container.source.batches("resources", None, 1))


@pytest.mark.parametrize(
    "content", ['{"secret":"private-data"}', '[{"id":"private-data"}]', "[invalid"]
)
def test_invalid_fixture_static_error(container, content):
    (container.source.root / "resources.json").write_text(content)
    with pytest.raises(DomainError) as caught:
        next(container.source.batches("resources", None, 1))
    assert "private-data" not in str(caught.value)
