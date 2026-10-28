"""Real PostgreSQL lease/fencing contract. SQLite tests are not a substitute."""
import pytest

from tests.unit.test_reconciliation_light_deep import local_file as local_file
from tests.unit.test_reconciliation_scheduler import (
    test_claim_crash_recovery_and_obsolete_worker_is_fenced as test_claim_crash_recovery_and_obsolete_worker_is_fenced,
)
from tests.unit.test_reconciliation_scheduler import (
    test_deep_runs_release_database_and_do_not_rehash_fresh_evidence as test_deep_runs_release_database_and_do_not_rehash_fresh_evidence,
)
from tests.unit.test_reconciliation_scheduler import (
    test_schedule_keys_are_idempotent_and_sync_checkpoints_untouched as test_schedule_keys_are_idempotent_and_sync_checkpoints_untouched,
)
from tests.unit.test_reconciliation_scheduler import (
    test_scheduler_off_pauses_old_scheduled_jobs_but_allows_manual_jobs as test_scheduler_off_pauses_old_scheduled_jobs_but_allows_manual_jobs,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


def test_two_postgres_workers_race_for_same_job(container):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
    queue = AuditQueue(container.uow, container.settings)
    key = queue.enqueue()
    barrier = Barrier(2)
    def claim():
        barrier.wait(timeout=5)
        return AuditQueue(container.uow, container.settings).claim()
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(row is not None for row in claims) == 1
    assert next(row for row in claims if row)['id'] == key
