"""Run the background audit contract against isolated PostgreSQL transactions."""
import pytest

from tests.unit.test_file_reconciliation_jobs import (
    test_accepted_response_and_durable_progress_do_not_wait_for_source as test_accepted_response_and_durable_progress_do_not_wait_for_source,
)
from tests.unit.test_file_reconciliation_jobs import (
    test_database_fence_prevents_second_api_owner as test_database_fence_prevents_second_api_owner,
)
from tests.unit.test_file_reconciliation_jobs import (
    test_each_file_has_its_own_deadline as test_each_file_has_its_own_deadline,
)
from tests.unit.test_file_reconciliation_jobs import (
    test_interrupted_run_resumes_unprocessed_versions_first as test_interrupted_run_resumes_unprocessed_versions_first,
)
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_reference import source_viewer as source_viewer

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    yield pg_container
    pg_container.reconciliation_jobs.close()
