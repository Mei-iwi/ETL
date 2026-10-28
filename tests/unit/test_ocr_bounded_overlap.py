"""SQLite claims are serialized by the fixture; PG contention remains a separate test."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest
from conftest import ingest

from data_sync_etl.application.media_capacity import media_capacity
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import OCRResult
from data_sync_etl.workers.ocr import OCRWorker


def test_ocr_global_admission_defaults_one_and_opt_in_two(container):
    version = ingest(container)
    container.ocr.create_job(version['id'])
    assert Settings(_env_file=None).ocr_max_active == 1
    first = container.ocr.claim('a')
    assert first and container.ocr.claim('b') is None
    container.ocr.max_active = 2
    second = container.ocr.claim('b')
    assert second and second['id'] != first['id']
    assert container.ocr.claim('c') is None


def test_two_ocr_tasks_overlap_with_global_cap_two(container, monkeypatch):
    version = ingest(container)
    container.ocr.create_job(version['id'])
    container.ocr.max_active = 2
    database, meter, barrier = Lock(), Lock(), Barrier(2)
    active, peak = 0, 0
    task_ids = []
    # SQLite fixture has one connection; only recognition overlaps. No claim of
    # cross-process/PG race validation is made by this fixture.
    for name in ('claim', 'succeed', 'fail', 'recover_stale'):
        original = getattr(container.ocr, name)
        def guarded(*args, _original=original, **kwargs):
            with database:
                return _original(*args, **kwargs)
        monkeypatch.setattr(container.ocr, name, guarded)
    def recognize(task):
        nonlocal active, peak
        with meter:
            active += 1
            peak = max(peak, active)
            task_ids.append(task['id'])
        barrier.wait(timeout=3)
        with meter:
            active -= 1
        return OCRResult('bounded fixture text'), None
    monkeypatch.setattr(container.ocr, 'recognize', recognize)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda worker: OCRWorker(container.ocr).once(worker), ('a', 'b')))
    assert results == [True, True] and peak == 2 and len(set(task_ids)) == 2
    with container.uow() as repo:
        assert repo.count('ocr_page_results', {}) == 2


def test_heavy_media_slot_excludes_another_worker_and_capacity_wait_is_not_failure(container):
    def other_worker():
        with media_capacity(container.uow, 1, .05):
            pytest.fail('second heavy model must not enter')
    with media_capacity(container.uow, 1, .5):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(DomainError, match='MEDIA_CAPACITY_TIMEOUT'):
                pool.submit(other_worker).result(timeout=2)
    with media_capacity(container.uow, 1, .5):
        pass  # slot released after the first attempt
    version = ingest(container)
    job = container.ocr.create_job(version['id'])
    for _ in range(container.ocr.max_retries + 1):
        task = container.ocr.claim('capacity-wait')
        assert container.ocr.fail(task, DomainError('MEDIA_CAPACITY_TIMEOUT'))
    with container.uow() as repo:
        assert repo.get('ocr_page_tasks', task['id'])['retry_count'] == 0
        assert repo.get('ocr_page_tasks', task['id'])['status'] == 'PENDING'
        assert repo.get('ocr_jobs', job['id'])['failed_pages'] == 0


@pytest.mark.parametrize('value', [0, 3, -1])
def test_invalid_ocr_admission(value):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ocr_max_active=value)
