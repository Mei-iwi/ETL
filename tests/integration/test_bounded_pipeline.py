from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import ingest
from pymongo import MongoClient

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.composition import Container
from tests.unit.test_bounded_workers import (
    test_expired_import_cannot_create_processing_job as test_expired_import_cannot_create_processing_job,
)
from tests.unit.test_source_file_import import importing as importing
from tests.unit.test_source_file_import import (
    test_crash_after_registration_resumes_without_recopy as test_crash_after_registration_resumes_without_recopy,
)
from tests.unit.test_source_file_import import (
    test_expired_lease_fences_old_worker as test_expired_lease_fences_old_worker,
)
from tests.unit.test_source_file_import import (
    test_import_native_pdf_runs_existing_pipeline_without_duplicates as test_import_native_pdf_runs_existing_pipeline_without_duplicates,
)
from tests.unit.test_source_file_import import (
    test_manual_version_collision_is_blocked as test_manual_version_collision_is_blocked,
)
from tests.unit.test_source_file_import import (
    test_multiple_versions_same_bytes_keep_identity as test_multiple_versions_same_bytes_keep_identity,
)
from tests.unit.test_source_file_import import (
    test_permanent_fault_does_not_register_or_hot_retry as test_permanent_fault_does_not_register_or_hot_retry,
)
from tests.unit.test_source_file_import import (
    test_source_mutation_is_not_overwritten as test_source_mutation_is_not_overwritten,
)
from tests.unit.test_source_lineage import (
    test_mapping_must_be_committed_same_origin_resource as test_mapping_must_be_committed_same_origin_resource,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


def test_two_ocr_workers_native_pdfs_real_mongo(container, mongo_test_database, monkeypatch):
    c = container
    uri, name = mongo_test_database
    client = MongoClient(uri, serverSelectionTimeoutMS=3000)
    db = client[name]
    first = ingest(c)
    second = c.ingestion.run('resource-2', 'input', 'sample.pdf')['version']
    jobs = [c.ocr.create_job(version['id']) for version in (first, second)]
    workers = [Container(c.settings, c.engine, MongoContentStore(db)) for _ in range(2)]
    barrier = Barrier(2)
    def overlap(original):
        entered = False
        def recognize(task):
            nonlocal entered
            if not entered:
                entered = True
                barrier.wait(timeout=10)
            return original(task)
        return recognize
    for worker in workers:
        monkeypatch.setattr(worker.ocr, 'recognize', overlap(worker.ocr.recognize))

    def drain(index):
        worked = 0
        while workers[index].worker.once(f'ocr-{index}'):
            worked += 1
            assert worked < 20
        return worked

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(drain, range(2)))
        assert all(counts)
        # Reconcile the crash gap/contended terminal callback through the existing path.
        for job in jobs:
            workers[0].processing_finalizer.finalize(job['id'])
        with c.uow() as repo:
            for job in jobs:
                current = repo.get('ocr_jobs', job['id'])
                assert current['status'] == 'COMPLETED'
                assert current['completed_pages'] == current['total_pages'] == 3
                assert current['failed_pages'] == 0
            assert repo.count('ocr_page_results', {}) == 6
            units = repo.count('content_units', {})
            assert units > 0
            stages = repo.find('etl_stage_runs', {'stage': 'MONGO_PROJECTION'})
            assert len(stages) == 2 and all(s['status'] == 'COMPLETED' for s in stages)
        assert db.ocr_pages.count_documents({}) == 6
        assert db.mongo_content_units.count_documents({}) == units
        assert db.mongo_content_units.count_documents({'is_current': True}) == units
    finally:
        client.close()


def test_two_file_services_import_independent_resources_with_overlap(importing, monkeypatch):
    from conftest import MemoryDocumentStore
    c, rows, path = importing
    c.settings.source_file_import_max_active = 2
    rows.append({**rows[0], 'source_version_id': 'source-other-1', 'resource_id': 'resource-2'})
    original_bytes = path.read_bytes()
    assert c.source_file_import.discover_once() == 2
    other = Container(c.settings.model_copy(deep=True), c.engine, MemoryDocumentStore())
    monkeypatch.setattr(other.source, 'get_version', c.source.get_version, raising=False)
    barrier = Barrier(2)
    def overlap(original):
        def run(claim):
            barrier.wait(timeout=10)
            return original(claim)
        return run
    services = [c.source_file_import, other.source_file_import]
    for service in services:
        monkeypatch.setattr(service, '_import', overlap(service._import))
    claims = [service.claim(f'file-{index}') for index, service in enumerate(services)]
    assert all(claims) and claims[0]['resource_id'] != claims[1]['resource_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda index: services[index].execute(claims[index]), range(2)))
    with c.uow() as repo:
        imports = repo.find('source_file_imports')
        assert len(imports) == 2 and all(row['status'] == 'PROCESSING' for row in imports)
        assert len({row['target_version_id'] for row in imports}) == 2
        assert len({row['processing_job_id'] for row in imports}) == 2
        assert repo.count('resource_versions', {}) == repo.count('ocr_jobs', {}) == 2
    assert path.read_bytes() == original_bytes
