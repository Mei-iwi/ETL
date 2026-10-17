"""B04 acceptance replacing the former defect-only diagnostics."""
import pytest

from tests.unit.test_source_file_import import discover_and_import
from tests.unit.test_source_file_import import importing as importing

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    pg_container.settings.sync_batch_size = 100
    return pg_container


def import_one(c):
    # Real discovery is driven by a new Master run/reconcile, not each UI poll.
    c.sync.run(full=True)
    return discover_and_import(c)


def test_source_branch_is_preserved(importing):
    c, rows, _ = importing
    template = dict(rows[0])
    first = import_one(c)
    rows.append({**template, "source_version_id": "source-version-2", "source_version_number": 2,
                 "parent_version_id": template["source_version_id"]})
    second = import_one(c)
    rows.append({**template, "source_version_id": "source-version-3", "source_version_number": 3,
                 "parent_version_id": template["source_version_id"]})
    third = import_one(c)
    with c.uow() as repo:
        target = repo.get("resource_versions", third["target_version_id"])
        assert target["parent_version_id"] == first["target_version_id"]
        assert target["parent_version_id"] != second["target_version_id"]


def test_source_late_number_is_imported(importing):
    c, rows, _ = importing
    template = dict(rows[0])
    root = import_one(c)
    rows.append({**template, "source_version_id": "source-version-3", "source_version_number": 3,
                 "parent_version_id": template["source_version_id"]})
    import_one(c)
    rows.append({**template, "source_version_id": "source-version-2", "source_version_number": 2,
                 "parent_version_id": template["source_version_id"]})
    late = import_one(c)
    assert late["target_version_id"]
    with c.uow() as repo:
        version = repo.get("resource_versions", late["target_version_id"])
        assert version["version_number"] == 2
        assert version["parent_version_id"] == root["target_version_id"]
