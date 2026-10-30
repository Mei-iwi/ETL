"""Run tree assertions against real isolated PostgreSQL transactions."""
import pytest

from tests.unit.test_source_file_import import importing as importing

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    pg_container.settings.sync_batch_size = 100
    return pg_container


def test_postgres_legacy_source_worker_remains_metadata_only(importing):
    c, _, _ = importing
    c.source_file_import.discover_once()
    assert c.source_file_import.claim() is None
    with c.uow() as repo:
        versions = repo.find("resource_versions", {"resource_id": "resource-1"}, order=("version_number",))
        assert versions and all(v["lifecycle_status"] == "METADATA_ONLY" for v in versions)
        assert repo.count("ocr_jobs", {}) == 0
