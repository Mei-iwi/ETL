from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from conftest import ingest
from pymongo import MongoClient

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.composition import Container
from tests.unit.test_source_file_import import importing as importing

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


def test_two_file_services_cannot_claim_external_content(importing):
    c, rows, path = importing
    rows.append({**rows[0], 'source_version_id': 'source-other-1', 'resource_id': 'resource-2'})
    original_bytes = path.read_bytes()
    assert c.source_file_import.discover_once() == 2
    assert c.source_file_import.claim('file-a') is None
    assert c.source_file_import.claim('file-b') is None
    with c.uow() as repo:
        imports = repo.find('source_file_imports')
        assert len(imports) == 2 and all(row['status'] == 'METADATA_ONLY' for row in imports)
        assert len({row['target_version_id'] for row in imports}) == 2
        assert all(row['claim_token'] is None for row in imports)
        assert repo.count('resource_versions', {}) == 2
        assert repo.count('ocr_jobs', {}) == 0
    assert path.read_bytes() == original_bytes
