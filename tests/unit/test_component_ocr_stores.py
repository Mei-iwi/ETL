import pytest
from sqlalchemy import create_engine, select

from data_sync_etl.adapters.mongo.component_content import (
    ComponentContentError,
    MongoComponentContent,
)
from data_sync_etl.adapters.persistence.component_ocr_outbox import (
    ComponentProjectionWorker,
    ComponentStateError,
    complete_verified_page,
    complete_verified_unit,
    component_metadata,
    component_outbox,
    project_pending,
    record_snapshot,
)
from data_sync_etl.adapters.persistence.component_ocr_projection import ProjectionError
from data_sync_etl.application.component_ocr_page import ComponentOCRPageExecutor
from data_sync_etl.db.ocr_component_models import (
    ComponentJobRef,
    ComponentPageRef,
    ComponentUnitRef,
)
from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import OCRResult
from tests.unit.test_component_ocr_projection import job, page, unit


class FakeCollection:
    def __init__(self):
        self.documents = {}

    def with_options(self, **kwargs):
        return self

    def update_one(self, selector, update, upsert):
        self.documents.setdefault(selector['_id'], update['$setOnInsert'].copy())

    def find_one(self, selector):
        item = self.documents.get(selector['_id'])
        return item.copy() if item else None

    def find(self, selector):
        return [self.documents[key].copy() for key in selector['_id']['$in'] if key in self.documents]


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def test_component_content_has_stable_ids_and_no_overwrite():
    store = MongoComponentContent(FakeDatabase())
    args = dict(namespace='a', version_id='v', generation=1, page_num=1,
                source_hash='a' * 64, fingerprint='b' * 64, raw_text='')
    first = store.put_page(**args)
    assert store.put_page(**args) == first
    assert store.put_page(**(args | {'namespace': 'b'})) != first
    assert store.get_page(**{k: v for k, v in args.items() if k != 'raw_text'})['raw_text'] == ''
    manifest_args = {k: v for k, v in args.items() if k not in ('page_num', 'raw_text')}
    store.put_manifest(**manifest_args, total_pages=2)
    assert store.get_manifest(namespace='a', version_id='v', generation=1)['total_pages'] == 2
    probe_args = dict(namespace='a', version_id='v', generation=1, source_hash='a' * 64, fingerprint='b' * 64)
    assert store.probe(authorized=False, **probe_args).status == 'UNAUTHORIZED'
    assert store.probe(authorized=True, **probe_args).missing_pages == (2,)
    store.put_page(**(args | {'page_num': 2}))
    assert store.probe(authorized=True, **probe_args).status == 'COMPLETE_VERIFIED'
    with pytest.raises(ComponentContentError, match='CONFLICT'):
        store.put_manifest(**manifest_args, total_pages=3)
    with pytest.raises(ComponentContentError, match='CONFLICT'):
        store.put_page(**(args | {'raw_text': 'changed'}))
    unit_args = {k: v for k, v in args.items() if k not in ('page_num', 'raw_text')}
    store.put_unit(**unit_args, unit_id='part-1', text='text')
    assert store.get_unit(**unit_args, unit_id='part-1')['text'] == 'text'
    store.pages.documents[first]['raw_text'] = 'corrupted'
    with pytest.raises(ComponentContentError, match='CORRUPT'):
        store.get_page(**{k: v for k, v in args.items() if k != 'raw_text'})


def test_outbox_replays_after_master_commit_before_component_ack():
    component = create_engine('sqlite:///:memory:')
    master = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        model.__table__.create(master)
    with component.begin() as connection:
        for kind, data in (('job', job()), ('page', page(status='PENDING', verified=False)),
                           ('unit', unit(verified=False, retrieval_eligible=False))):
            assert record_snapshot(connection, kind, data)
            assert record_snapshot(connection, kind, data)
    with component.connect() as connection:
        transaction = connection.begin()
        assert project_pending(connection, master, lambda *_: True, lambda *_: True) == 3
        transaction.rollback()  # process dies after Master commit, before ACK
    with component.begin() as connection:
        assert len(connection.execute(select(component_outbox)).all()) == 3
    worker = ComponentProjectionWorker(component, master, verify_mapping=lambda *_: True,
                                       verify_isolation=lambda *_: True)
    assert worker.run_once() == 3
    assert worker.run_once() == 0
    with master.begin() as connection:
        for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
            assert len(connection.execute(select(model.__table__)).all()) == 1
    component.dispose()
    master.dispose()


def test_tombstone_cannot_be_revived_by_newer_revision():
    component = create_engine('sqlite:///:memory:')
    master = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    ComponentJobRef.__table__.create(master)
    worker = ComponentProjectionWorker(component, master, verify_mapping=lambda *_: True,
                                       verify_isolation=lambda *_: True)
    with component.begin() as conn:
        record_snapshot(conn, 'job', job(revision=2, tombstone=True))
    assert worker.run_once() == 1
    with component.begin() as conn:
        with pytest.raises(ComponentStateError, match='TOMBSTONE_CONFLICT'):
            record_snapshot(conn, 'job', job(revision=3, tombstone=False))
    with master.begin() as conn:
        row = conn.execute(select(ComponentJobRef.__table__)).mappings().one()
        assert row['tombstone'] is True and row['revision'] == 2
    component.dispose()
    master.dispose()


def test_worker_defers_children_until_parents_in_same_batch():
    component = create_engine('sqlite:///:memory:')
    master = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        model.__table__.create(master)
    with component.begin() as conn:
        record_snapshot(conn, 'page', page(status='PENDING', verified=False))
        record_snapshot(conn, 'unit', unit(source_unit_id='child', parent_unit_id='parent',
                                           verified=False, retrieval_eligible=False))
        record_snapshot(conn, 'unit', unit(source_unit_id='parent', verified=False, retrieval_eligible=False))
        record_snapshot(conn, 'job', job())
    worker = ComponentProjectionWorker(component, master, verify_mapping=lambda *_: True,
                                       verify_isolation=lambda *_: True)
    assert worker.run_once() == 4
    with master.begin() as conn:
        assert len(conn.execute(select(ComponentUnitRef.__table__)).all()) == 2
    component.dispose()
    master.dispose()


def test_verified_content_readback_before_component_commit_and_retry():
    component = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    store = MongoComponentContent(FakeDatabase())
    with component.begin() as conn:
        record_snapshot(conn, 'job', job())
        with pytest.raises(ComponentStateError, match='REQUIRES_CONTENT_CHECK'):
            record_snapshot(conn, 'page', page())
        with pytest.raises(ComponentStateError, match='NOT_VERIFIED'):
            complete_verified_page(conn, store, page(), source_version_id='version-1',
                                   source_hash='a' * 64, fingerprint='b' * 64)
    store.put_page(namespace='source-a', version_id='version-1', generation=1, page_num=1,
                   source_hash='a' * 64, fingerprint='b' * 64, raw_text='')
    unit_args = dict(namespace='source-a', version_id='version-1', generation=1, unit_id='unit-1',
                     source_hash='a' * 64, fingerprint='b' * 64)
    store.put_unit(**unit_args, text='unit text')
    checksum = store.get_unit(**unit_args)['checksum']
    with component.begin() as conn:
        for _ in range(2):
            assert complete_verified_page(conn, store, page(), source_version_id='version-1',
                                          source_hash='a' * 64, fingerprint='b' * 64)
            assert complete_verified_unit(conn, store, unit(text_checksum=checksum),
                                          source_hash='a' * 64, fingerprint='b' * 64)
        assert len(conn.execute(select(component_outbox)).all()) == 3
    component.dispose()


def test_job_completion_requires_every_verified_page():
    component = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    store = MongoComponentContent(FakeDatabase())
    with component.begin() as conn:
        record_snapshot(conn, 'job', job())
        with pytest.raises(ComponentStateError, match='JOB_NOT_VERIFIED'):
            record_snapshot(conn, 'job', job(status='COMPLETED', revision=2, completed_pages=2))
        for number in (1, 2):
            store.put_page(namespace='source-a', version_id='version-1', generation=1, page_num=number,
                           source_hash='a' * 64, fingerprint='b' * 64, raw_text=f'page {number}')
            complete_verified_page(conn, store, page(source_task_id=f'task-{number}', page_num=number),
                                   source_version_id='version-1', source_hash='a' * 64, fingerprint='b' * 64)
        assert record_snapshot(conn, 'job', job(status='COMPLETED', revision=2, completed_pages=2))
        with pytest.raises(ComponentStateError, match='TERMINAL_JOB_CONFLICT'):
            record_snapshot(conn, 'job', job(status='RUNNING', revision=3, completed_pages=2))
        with pytest.raises(ComponentStateError, match='VERIFIED_CONTENT_CONFLICT'):
            record_snapshot(conn, 'page', page(source_task_id='task-1', revision=2,
                                               status='PENDING', verified=False))
    component.dispose()


def test_page_executor_recovers_content_after_lost_lease_without_second_ocr():
    from contextlib import contextmanager
    from io import BytesIO

    component = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    store = MongoComponentContent(FakeDatabase())
    with component.begin() as conn:
        record_snapshot(conn, 'job', job())
    calls = []

    @contextmanager
    def opener():
        calls.append('open')
        yield BytesIO(b'file')

    def recognize(stream, number):
        calls.append(('ocr', number))
        return OCRResult('result', engine_name='test', metadata={'source_type': 'pdf'})

    lease = iter([True, False])
    executor = ComponentOCRPageExecutor(component, store, authorize=lambda *_: True,
                                       check_lease=lambda *_: next(lease), open_verified=opener,
                                       recognize=recognize)
    args = dict(source_version_id='version-1', source_hash='a' * 64, fingerprint='b' * 64)
    with pytest.raises(DomainError, match='LEASE_LOST'):
        executor.run(page(), **args)
    executor.check_lease = lambda *_: True
    assert executor.run(page(), **args)['reused'] is True
    assert calls == ['open', ('ocr', 1)]
    executor.authorize = lambda *_: False
    with pytest.raises(DomainError, match='UNAUTHORIZED'):
        executor.run(page(), **args)
    assert calls == ['open', ('ocr', 1)]
    component.dispose()


def test_isolated_processing_outbox_and_three_master_replicas():
    from contextlib import contextmanager
    from io import BytesIO

    component = create_engine('sqlite:///:memory:')
    master = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        model.__table__.create(master)
    store = MongoComponentContent(FakeDatabase())
    calls = []

    @contextmanager
    def opener():
        calls.append('open')
        yield BytesIO(b'fixture')

    executor = ComponentOCRPageExecutor(
        component, store, authorize=lambda *_: True, check_lease=lambda *_: True,
        open_verified=opener, recognize=lambda _, n: OCRResult(f'text-{n}', engine_name='test'),
    )
    with component.begin() as conn:
        record_snapshot(conn, 'job', job())
    args = dict(source_version_id='version-1', source_hash='a' * 64, fingerprint='b' * 64)
    for number in (1, 2):
        assert not executor.run(page(source_task_id=f'task-{number}', page_num=number), **args)['reused']
    assert executor.run(page(), **args)['reused']
    assert calls == ['open', 'open']
    unit_args = dict(namespace='source-a', version_id='version-1', generation=1, unit_id='unit-1',
                     source_hash='a' * 64, fingerprint='b' * 64)
    store.put_unit(**unit_args, text='normalized text')
    checksum = store.get_unit(**unit_args)['checksum']
    with component.begin() as conn:
        record_snapshot(conn, 'job', job(status='COMPLETED', revision=2, completed_pages=2))
        complete_verified_unit(conn, store, unit(text_checksum=checksum), source_hash='a' * 64,
                               fingerprint='b' * 64)
    worker = ComponentProjectionWorker(component, master, verify_mapping=lambda *_: True,
                                       verify_isolation=lambda *_: True)
    assert worker.run_once() == 5
    with master.begin() as conn:
        assert conn.execute(select(ComponentJobRef.status)).scalar_one() == 'COMPLETED'
        assert len(conn.execute(select(ComponentPageRef.__table__)).all()) == 2
        assert len(conn.execute(select(ComponentUnitRef.__table__)).all()) == 1
        for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
            assert not {'raw_text', 'text', 'normalized_text'} & set(model.__table__.columns.keys())
    component.dispose()
    master.dispose()


def test_outbox_fails_closed_and_rolls_back_invalid_projection():
    component = create_engine('sqlite:///:memory:')
    master = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    ComponentJobRef.__table__.create(master)
    with component.begin() as conn:
        record_snapshot(conn, 'job', job())
        with pytest.raises(ComponentStateError, match='INVALID_FIELDS'):
            record_snapshot(conn, 'job', job(raw_text='not allowed'))
    with component.begin() as conn:
        with pytest.raises(ComponentStateError, match='NOT_ISOLATED'):
            project_pending(conn, master, lambda *_: True, lambda *_: False)
        with pytest.raises(ProjectionError, match='NOT_AUTHORIZED'):
            project_pending(conn, master, lambda *_: False, lambda *_: True)
        assert conn.execute(select(component_outbox.c.delivered)).scalar_one() is False
    component.dispose()
    master.dispose()
