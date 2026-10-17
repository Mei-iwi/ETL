"""Run tree assertions against real isolated PostgreSQL transactions."""
import pytest

from tests.unit.test_source_file_import import importing as importing
from tests.unit.test_source_lineage import (  # noqa: F401
    test_historical_wrong_tree_blocks_descendant_without_repair,
    test_inactive_source_resource_blocks_before_bytes,
    test_invalid_parent_never_copies_bytes,
    test_mapping_must_be_committed_same_origin_resource,
    test_null_source_parent_stays_null,
    test_parent_arrival_automatically_recovers_before_retry_budget,
    test_parent_pending_bounded_and_manual_recovery,
    test_source_branch_and_late_number,
    test_source_parent_changes_during_copy_rolls_back_mapping,
    test_successful_parent_change_is_reported_without_repair,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    pg_container.settings.sync_batch_size = 100
    return pg_container


def test_concurrent_siblings_preserve_parent_and_claim_fencing(importing):
    from concurrent.futures import ThreadPoolExecutor

    from tests.unit.test_source_lineage import add_version, run
    c, rows, _ = importing
    root = run(c)
    add_version(rows, 3, rows[0]["source_version_id"])
    add_version(rows, 2, rows[0]["source_version_id"])
    c.sync.run(full=True)
    c.source_file_import.discover_once()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: c.source_file_import.claim(), range(2)))
        assert all(claims) and claims[0]["id"] != claims[1]["id"]
        list(pool.map(c.source_file_import.execute, claims))
    # Expired/reused claims must not register new targets.
    for claim in claims:
        c.source_file_import.execute(claim)
    with c.uow() as repo:
        versions = repo.find("resource_versions", {"resource_id": "resource-1"}, order=("version_number",))
        assert [v["version_number"] for v in versions] == [1, 2, 3]
        assert [v["parent_version_id"] for v in versions] == [None, root["target_version_id"], root["target_version_id"]]
