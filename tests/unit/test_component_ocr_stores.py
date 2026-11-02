import pytest
from sqlalchemy import create_engine, select, update

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
from data_sync_etl.application.component_ocr_page import ComponentOCRPageExecutor
from data_sync_etl.db.ocr_component_models import (
    ComponentJobRef,
    ComponentPageRef,
    ComponentUnitRef,
)
from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.processing import OCRResult
from data_sync_etl.workers.ocr import OCRWorker
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


def test_parent_in_later_batch_does_not_block_other_events():
    component = create_engine('sqlite:///:memory:')
    master = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        model.__table__.create(master)
    with component.begin() as conn:
        record_snapshot(conn, 'page', page(status='PENDING', verified=False))
        record_snapshot(conn, 'job', job())
    worker = ComponentProjectionWorker(component, master, verify_mapping=lambda *_: True,
                                       verify_isolation=lambda *_: True)
    assert worker.run_once(limit=1) == 0
    with component.begin() as conn:
        row = conn.execute(select(component_outbox).order_by(component_outbox.c.id)).mappings().first()
        assert row['attempts'] == 1 and row['last_error_code'] == 'PARENT_NOT_READY'
    assert worker.run_once(limit=1) == 1
    with component.begin() as conn:
        conn.execute(update(component_outbox).values(next_attempt_at=None))
    assert worker.run_once(limit=1) == 1
    with master.begin() as conn:
        assert len(conn.execute(select(ComponentPageRef.__table__)).all()) == 1
    component.dispose()
    master.dispose()


def test_existing_ocr_worker_runs_injected_projection_and_preserves_fairness():
    class IdlePipeline:
        def recover_stale(self):
            return {'recovered': 0}

        def schedule_authorized_source_job(self):
            return False

        def claim(self, worker_id):
            return None

    calls = []

    class Projection:
        def run_once(self):
            calls.append('projection')
            return 1

    worker = OCRWorker(IdlePipeline(), reconcile=lambda: calls.append('index') or True,
                       component_projection_worker=Projection())
    assert worker.once('test') is True
    assert worker.once('test') is True
    assert calls == ['projection', 'index']


def test_actual_ocr_worker_routes_component_claim_to_component_executor():
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

    executor = ComponentOCRPageExecutor(
        component, store, authorize=lambda *_: True, check_lease=lambda *_: True,
        open_verified=opener, recognize=lambda _, n: OCRResult(f'text-{n}', engine_name='test'),
    )
    task = {'id': 'task-1', 'job_id': 'job-1', 'component_snapshot': page(),
            'source_version_id': 'version-1', 'source_hash': 'a' * 64, 'processing_fingerprint': 'b' * 64}

    class ComponentPipeline:
        component_owned = True
        heartbeat_timeout = 300

        def recover_stale(self):
            return {'recovered': 0}

        def claim(self, worker_id):
            return task

        def fail(self, task, error):
            raise AssertionError('component path unexpectedly failed') from error

    worker = OCRWorker(ComponentPipeline(), component_page_executor=executor)
    assert worker.once('worker-test')
    assert worker.once('worker-test')
    assert calls == ['open']
    with component.begin() as conn:
        events = conn.execute(select(component_outbox)).mappings().all()
        assert len(events) == 2
        assert all('raw_text' not in row['snapshot'] for row in events)
    with pytest.raises(ValueError, match='WORKER_NOT_CONFIGURED'):
        OCRWorker(ComponentPipeline())
    component.dispose()


def test_component_worker_error_never_falls_back_to_legacy_ocr():
    task = {'id': 'task-1', 'job_id': 'job-1'}
    calls = []

    class ComponentPipeline:
        component_owned = True
        heartbeat_timeout = 300

        def recover_stale(self):
            return {'recovered': 0}

        def claim(self, worker_id):
            return task

        def recognize(self, claimed):
            raise AssertionError('must not use legacy OCR')

        def succeed(self, *args):
            raise AssertionError('must not write legacy results')

        def fail(self, claimed, error):
            calls.append((claimed, str(error)))

    class Executor:
        def execute_claimed(self, claimed):
            raise DomainError('COMPONENT_CONTENT_UNAVAILABLE')

    worker = OCRWorker(ComponentPipeline(), component_page_executor=Executor())
    assert worker.once('worker-test')
    assert calls == [(task, 'COMPONENT_CONTENT_UNAVAILABLE')]


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
    store.put_manifest(namespace='source-a', version_id='version-1', generation=1, total_pages=2,
                       source_hash='a' * 64, fingerprint='b' * 64)
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


def test_component_executor_rejects_stale_manifest_before_opening_source():
    component = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    store = MongoComponentContent(FakeDatabase())
    with component.begin() as conn:
        record_snapshot(conn, 'job', job())
    calls = []
    executor = ComponentOCRPageExecutor(component, store, authorize=lambda *_: True,
                                       check_lease=lambda *_: True,
                                       open_verified=lambda: calls.append('open'), recognize=lambda *_: None)
    args = dict(source_version_id='version-1', source_hash='a' * 64, fingerprint='b' * 64)
    store.put_manifest(namespace='source-a', version_id='version-1', generation=1, total_pages=2,
                       source_hash='c' * 64, fingerprint='b' * 64)
    with pytest.raises(DomainError, match='MANIFEST_STALE'):
        executor.run(page(), **args)
    assert calls == []
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
    store.put_manifest(namespace='source-a', version_id='version-1', generation=1, total_pages=2,
                       source_hash='a' * 64, fingerprint='b' * 64)
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
        assert project_pending(conn, master, lambda *_: False, lambda *_: True) == 0
        row = conn.execute(select(component_outbox)).mappings().one()
        assert row['delivered'] is False and row['last_error_code'] == 'IDENTITY_NOT_AUTHORIZED'
    component.dispose()
    master.dispose()


def test_authority_outage_defers_event_without_storing_error_details():
    component = create_engine('sqlite:///:memory:')
    master = create_engine('sqlite:///:memory:')
    component_metadata.create_all(component)
    ComponentJobRef.__table__.create(master)
    with component.begin() as conn:
        record_snapshot(conn, 'job', job())

    def unavailable(*_):
        raise DomainError('sensitive internal detail')

    worker = ComponentProjectionWorker(component, master, verify_mapping=unavailable,
                                       verify_isolation=lambda *_: True)
    assert worker.run_once() == 0
    with component.begin() as conn:
        row = conn.execute(select(component_outbox)).mappings().one()
        assert row['last_error_code'] == 'AUTHORITY_UNAVAILABLE'
        assert row['delivered'] is False and row['attempts'] == 1
    component.dispose()
    master.dispose()
