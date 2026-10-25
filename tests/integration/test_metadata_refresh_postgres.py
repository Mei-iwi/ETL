"""Refresh checkpoints and advisory locks on an isolated PostgreSQL schema."""
import pytest

from tests.unit.test_metadata_master_refresh import (
    test_idle_existing_worker_refreshes_metadata_after_commit as test_idle_existing_worker_refreshes_metadata_after_commit,
)
from tests.unit.test_metadata_master_refresh import (
    test_retry_preserves_cursor_and_replays_failed_resource as test_retry_preserves_cursor_and_replays_failed_resource,
)
from tests.unit.test_metadata_search import metadata as metadata

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container
