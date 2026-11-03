from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from sqlalchemy import create_engine, select, update

from data_sync_etl.adapters.mongo.component_content import MongoComponentContent
from data_sync_etl.adapters.persistence.component_ocr_lease import (
    ComponentOCRLeaseQueue,
    component_page_leases,
)
from data_sync_etl.adapters.persistence.component_ocr_outbox import (
    component_metadata,
    component_tables,
)
from data_sync_etl.application.component_ocr_page import ComponentOCRPageExecutor
from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import OCRResult
from data_sync_etl.workers.ocr import OCRWorker
from tests.unit.test_component_ocr_stores import FakeDatabase


def submit(queue, **changes):
    data = dict(namespace='source-a', source_version_id='source-v', master_version_id='master-v',
                master_resource_id='master-r', generation=1, source_hash='a' * 64,
                processing_fingerprint='b' * 64, total_pages=2)
    return queue.submit(**(data | changes))


def test_component_queue_worker_claim_completion_and_idempotent_submission():
    engine = create_engine('sqlite:///:memory:')
    component_metadata.create_all(engine)
    queue = ComponentOCRLeaseQueue(engine, verify_mapping=lambda *_: True)
    job_id = submit(queue)
    assert submit(queue) == job_id
    with pytest.raises(DomainError, match='GENERATION_CONFLICT'):
        submit(queue, source_hash='c' * 64)
    content = MongoComponentContent(FakeDatabase())
    opens = []

    @contextmanager
    def opener():
        opens.append('open')
        yield BytesIO(b'fixture')

    executor = ComponentOCRPageExecutor(
        engine, content, authorize=lambda *_: True, check_lease=queue.owns, open_verified=opener,
        recognize=lambda _stream, page: OCRResult(f'page-{page}', engine_name='test'),
        after_complete=queue.after_complete,
    )
    worker = OCRWorker(queue, component_page_executor=executor)
    assert worker.once('worker-a')
    assert worker.once('worker-a')
    assert worker.once('worker-a') is False
    with engine.begin() as conn:
        job = conn.execute(select(component_tables['job'])).mappings().one()
        pages = conn.execute(select(component_tables['page'])).mappings().all()
        assert job['status'] == 'COMPLETED' and job['completed_pages'] == 2
        assert len(pages) == 2 and all(p['status'] == 'SUCCEEDED' and p['verified'] for p in pages)
        assert all(row['claim_token'] is None for row in conn.execute(select(component_page_leases)).mappings())
    assert opens == ['open', 'open']
    assert submit(queue) == job_id
    assert worker.once('worker-a') is False
    engine.dispose()


def test_expired_lease_is_fenced_after_reclaim_and_retry_is_bounded():
    engine = create_engine('sqlite:///:memory:')
    component_metadata.create_all(engine)
    queue = ComponentOCRLeaseQueue(engine, verify_mapping=lambda *_: True, max_retries=1)
    submit(queue)
    old = queue.claim('old')
    with engine.begin() as conn:
        conn.execute(update(component_page_leases).where(
            component_page_leases.c.source_task_id == old['id']).values(
                lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
    new = queue.claim('new')
    assert new['id'] == old['id'] and new['claim_token'] != old['claim_token']
    assert queue.heartbeat(old) is False
    assert queue.fail(old) is False
    assert queue.fail(new) is True
    with engine.begin() as conn:
        page = conn.execute(select(component_tables['page']).where(
            component_tables['page'].c.source_task_id == old['id'])).mappings().one()
        assert page['status'] == 'FAILED' and page['retry_count'] == 1
    engine.dispose()


def test_submission_requires_server_authority_and_preserves_component_state():
    engine = create_engine('sqlite:///:memory:')
    component_metadata.create_all(engine)
    queue = ComponentOCRLeaseQueue(engine, verify_mapping=lambda *_: False)
    with pytest.raises(DomainError, match='UNAUTHORIZED'):
        submit(queue)
    with engine.begin() as conn:
        assert not conn.execute(select(component_tables['job'])).all()
    engine.dispose()


def test_namespaces_keep_matching_source_versions_separate():
    engine = create_engine('sqlite:///:memory:')
    component_metadata.create_all(engine)
    queue = ComponentOCRLeaseQueue(engine, verify_mapping=lambda *_: True)
    assert submit(queue, namespace='source-a') != submit(queue, namespace='source-b')
    with engine.begin() as conn:
        assert len(conn.execute(select(component_tables['job'])).all()) == 2
    engine.dispose()
