from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace

import pytest
from conftest import ingest
from sqlalchemy import create_engine, event, select

from data_sync_etl.adapters.mongo.component_content import MongoComponentContent
from data_sync_etl.adapters.persistence.component_ocr_lease import ComponentOCRLeaseQueue
from data_sync_etl.adapters.persistence.component_ocr_outbox import (
    component_metadata,
    component_tables,
)
from data_sync_etl.adapters.processing.component_ocr_postprocess import ComponentOCRPostprocessor
from data_sync_etl.adapters.processing.component_ocr_source import ComponentOCRSourceBridge
from data_sync_etl.domain.core import DomainError, normalize_text
from data_sync_etl.workers.ocr import OCRWorker
from tests.unit.test_component_ocr_stores import FakeDatabase


def test_component_worker_uses_existing_native_pdf_extraction_without_master_raw_write(container):
    version = ingest(container, texts=['The verified component document contains enough native text for extraction.'])
    original = container.storage.get_local_path('input', 'sample.pdf').read_bytes()
    reference = SimpleNamespace(ownership='SOURCE_BACKED', source_version_id='source-version',
                                expected_sha256=version['source_hash'], source_id='source-a', filename='sample.pdf',
                                resource_id=version['resource_id'])
    reads = []

    class Resolver:
        source_registry = SimpleNamespace(registrations=lambda: [SimpleNamespace(
            source_id='source-a', namespace='ns-a', enabled=True)])

        def resolve(self, version_id):
            assert version_id == version['id']
            return reference

        def _capability_source_id(self, ref):
            return ref.source_id

        def require_content_capabilities(self, ref, *, index):
            assert index and ref is reference

        @contextmanager
        def open_verified_stream(self, ref, *, index):
            assert ref is reference and index
            reads.append('open')
            yield BytesIO(original)

    engine = create_engine('sqlite:///:memory:')
    component_metadata.create_all(engine)
    queue = ComponentOCRLeaseQueue(engine, verify_mapping=lambda *_: True)
    content = MongoComponentContent(FakeDatabase())
    bridge = ComponentOCRSourceBridge(queue, content, Resolver(), lambda *_: True, container.ocr)
    postprocessor = ComponentOCRPostprocessor(engine, content, verify_mapping=lambda *_: True)
    job_id = bridge.submit_version(version['id'], generation=1, processing_fingerprint='b' * 64)
    statements = []

    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lower())

    event.listen(container.engine, 'before_cursor_execute', capture)
    try:
        worker = OCRWorker(queue, component_page_executor=bridge.executor(), on_job_terminal=postprocessor.run_job)
        assert worker.once('component-worker')
        assert not worker.once('component-worker')
    finally:
        event.remove(container.engine, 'before_cursor_execute', capture)
    assert reads == ['open', 'open']  # page count, then actual extraction
    assert bridge.submit_version(version['id'], generation=1, processing_fingerprint='b' * 64) == job_id
    assert reads == ['open', 'open']  # verified manifest avoids opening the file again
    assert not any(('insert' in sql or 'update' in sql) and
                   ('ocr_page_results' in sql or 'content_unit_texts' in sql) for sql in statements)
    with engine.begin() as conn:
        assert conn.execute(select(component_tables['job'].c.status)).scalar_one() == 'COMPLETED'
        units = conn.execute(select(component_tables['unit'])).mappings().all()
        assert len(units) == 1 and units[0]['verified'] and not units[0]['retrieval_eligible']
    result = content.get_page(namespace='ns-a', version_id='source-version', generation=1, page_num=1,
                              source_hash=version['source_hash'], fingerprint='b' * 64)
    assert 'verified component document' in result['raw_text']
    unit_text = content.get_unit(namespace='ns-a', version_id='source-version', generation=1,
                                 unit_id=units[0]['source_unit_id'], source_hash=version['source_hash'],
                                 fingerprint='b' * 64)
    assert unit_text['text'] == normalize_text(result['raw_text'])
    assert postprocessor.run_job(job_id)['content_unit_count'] == 1  # safe replay
    engine.dispose()


def test_component_bridge_rejects_wrong_namespace_before_read():
    reference = SimpleNamespace(ownership='SOURCE_BACKED', source_version_id='v', expected_sha256='a' * 64,
                                source_id='s', filename='sample.pdf')
    registry = SimpleNamespace(registrations=lambda: [SimpleNamespace(source_id='s', namespace='right', enabled=True)])
    resolver = SimpleNamespace(resolve=lambda _: reference, _capability_source_id=lambda _: 's', source_registry=registry)
    bridge = ComponentOCRSourceBridge(None, None, resolver, lambda *_: True, None)
    with pytest.raises(DomainError, match='IDENTITY_MISMATCH'):
        bridge.authorize({'master_version_id': 'm', 'namespace': 'wrong'}, 'v', 'a' * 64)


def test_partial_existing_ocr_opens_source_only_for_missing_page(container):
    version = ingest(container, texts=[f'This verified component test has enough native text for page {n}.'
                                       for n in (1, 2, 3)])
    original = container.storage.get_local_path('input', 'sample.pdf').read_bytes()
    reference = SimpleNamespace(ownership='SOURCE_BACKED', source_version_id='source-version',
                                expected_sha256=version['source_hash'], source_id='source-a', filename='sample.pdf',
                                resource_id=version['resource_id'])
    reads = []

    class Resolver:
        source_registry = SimpleNamespace(registrations=lambda: [SimpleNamespace(
            source_id='source-a', namespace='ns-a', enabled=True)])

        def resolve(self, version_id):
            assert version_id == version['id']
            return reference

        def _capability_source_id(self, ref):
            return ref.source_id

        def require_content_capabilities(self, ref, *, index):
            assert ref is reference and index

        @contextmanager
        def open_verified_stream(self, ref, *, index):
            assert ref is reference and index
            reads.append('open')
            yield BytesIO(original)

    engine = create_engine('sqlite:///:memory:')
    component_metadata.create_all(engine)
    queue = ComponentOCRLeaseQueue(engine, verify_mapping=lambda *_: True)
    content = MongoComponentContent(FakeDatabase())
    identity = dict(namespace='ns-a', version_id='source-version', generation=1,
                    source_hash=version['source_hash'], fingerprint='b' * 64)
    content.put_manifest(**identity, total_pages=3)
    content.put_page(**identity, page_num=1, raw_text='')  # empty text may be verified
    content.put_page(**identity, page_num=3, raw_text='existing page three')
    bridge = ComponentOCRSourceBridge(queue, content, Resolver(), lambda *_: True, container.ocr)
    bridge.submit_version(version['id'], generation=1, processing_fingerprint='b' * 64)
    assert reads == []  # existing manifest provides the page count
    worker = OCRWorker(queue, component_page_executor=bridge.executor())
    for _ in range(3):
        assert worker.once('component-worker')
    assert reads == ['open']
    assert content.get_page(**identity, page_num=1)['raw_text'] == ''
    assert 'page 2' in content.get_page(**identity, page_num=2)['raw_text']
    assert content.get_page(**identity, page_num=3)['raw_text'] == 'existing page three'
    with engine.begin() as conn:
        job = conn.execute(select(component_tables['job'])).mappings().one()
        assert job['status'] == 'COMPLETED' and job['completed_pages'] == 3
    engine.dispose()
