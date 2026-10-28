"""Real independent processes and connections; all writes stay in pg_container's schema."""
import multiprocessing as mp
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from sqlalchemy import event, select, text

from data_sync_etl.adapters.persistence.audit_queue import AuditQueue
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.config import Settings
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.core import new_id, now
from tests.unit.test_bounded_workers import (
    seed_imports,
)
from tests.unit.test_bounded_workers import (
    test_audit_stream_exclusion_and_capacity as test_audit_stream_exclusion_and_capacity,
)
from tests.unit.test_bounded_workers import (
    test_import_resource_order_capacity_and_recovery as test_import_resource_order_capacity_and_recovery,
)
from tests.unit.test_bounded_workers import (
    test_import_retry_wait_does_not_spend_child_attempts as test_import_retry_wait_does_not_spend_child_attempts,
)
from tests.unit.test_bounded_workers import (
    test_ocr_expired_owner_cannot_renew_succeed_or_fail as test_ocr_expired_owner_cannot_renew_succeed_or_fail,
)
from tests.unit.test_bounded_workers import (
    test_processing_fence_rolls_back_transaction as test_processing_fence_rolls_back_transaction,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


def process_queue(url, schema, mode, ready, start, output):
    engine = make_engine(url)
    assert schema.startswith('etl_test_') and len(schema) == 41
    statements = 0
    connections = 0

    @event.listens_for(engine, 'connect')
    def count_connections(*_):
        nonlocal connections
        connections += 1

    @event.listens_for(engine, 'before_cursor_execute')
    def count_statements(*_):
        nonlocal statements
        statements += 1

    @event.listens_for(engine, 'checkout')
    def search_path(connection, *_):
        previous = connection.autocommit
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        connection.autocommit = previous

    uow = SQLAlchemyUnitOfWork(sessions(engine))
    settings = Settings(_env_file=None, reconciliation_max_active=2)
    ready.put(True)
    assert start.wait(20)
    try:
        if mode.startswith('audit'):
            claim = AuditQueue(uow, settings).claim()
            output.put(claim)
        elif mode == 'crash':
            stamp = now()
            with uow() as repo:
                claim = repo.claim_source_import(stamp, new_id(), stamp + timedelta(seconds=1), 2)
            output.put(claim)
            time.sleep(30)  # parent deliberately terminates this process
        else:
            completed, tokens, latencies = [], [], []
            started = time.perf_counter()
            while True:
                before = time.perf_counter()
                stamp = now()
                with uow() as repo:
                    claim = repo.claim_source_import(stamp, new_id(), stamp + timedelta(seconds=60), int(mode))
                if claim is None:
                    with uow() as repo:
                        pending = repo.count('source_file_imports', {'status': 'PENDING'})
                    if not pending:
                        break
                    time.sleep(.005)
                    continue
                # Queue-only idempotent output, fenced by the real service guard.
                from data_sync_etl.application.source_file_import import SourceFileImportService
                with uow() as repo:
                    row = SourceFileImportService._owned(None, repo, claim)
                    repo.update('source_file_imports', row['id'], {
                        'status': 'METADATA_ONLY', 'claim_token': None, 'lease_expires_at': None})
                completed.append(claim['id'])
                tokens.append(claim['claim_token'])
                latencies.append(time.perf_counter() - before)
            output.put({'ids': completed, 'tokens': tokens, 'latencies': latencies,
                        'seconds': time.perf_counter() - started, 'sql_statements': statements,
                        'connections_opened': connections})
    finally:
        engine.dispose()


def start_processes(c, modes, timeout=60):
    context = mp.get_context('spawn')
    ready, output, start = context.Queue(), context.Queue(), context.Event()
    with c.engine.connect() as conn:
        schema = conn.scalar(text('SELECT current_schema()'))
    url = c.engine.url.render_as_string(hide_password=False)
    processes = [context.Process(target=process_queue,
        args=(url, schema, mode, ready, start, output)) for mode in modes]
    for process in processes:
        process.start()
    try:
        for _ in processes:
            assert ready.get(timeout=30)
        start.set()
        results = [output.get(timeout=timeout) for _ in processes]
        if 'crash' not in modes:
            for process in processes:
                process.join(15)
                assert process.exitcode == 0
        return processes, results
    finally:
        if 'crash' not in modes:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(10)


def test_two_processes_claim_100_imports_without_duplicate(container):
    rows = seed_imports(container.uow, resources=100)
    _, results = start_processes(container, ['2', '2'])
    ids = [key for result in results for key in result['ids']]
    tokens = [key for result in results for key in result['tokens']]
    assert len(ids) == len(set(ids)) == 100
    assert set(ids) == {row['id'] for row in rows}
    assert len(set(tokens)) == 100
    assert all(result['ids'] for result in results)
    with container.uow() as repo:
        assert repo.count('source_file_imports', {'status': 'METADATA_ONLY'}) == 100
        assert all(row['attempts'] == 1 for row in repo.find('source_file_imports'))


@pytest.mark.parametrize('streams,expected', [(['resources', 'resources'], 1),
                                            (['resources', 'subjects'], 2)])
def test_process_audit_admission(container, streams, expected):
    q = AuditQueue(container.uow, container.settings)
    for stream in streams:
        q.enqueue('METADATA', stream)
    _, claims = start_processes(container, ['audit', 'audit'])
    assert sum(claim is not None for claim in claims) == expected
    with container.uow() as repo:
        rows = list(repo.session.scalars(select(Job)))
        assert sum(row.attempts for row in rows) == expected
        assert all(row.attempts == 0 for row in rows if row.status == 'PENDING')


def test_process_crash_import_recovers_after_lease(container):
    seed_imports(container.uow)
    processes, claims = start_processes(container, ['crash'])
    try:
        processes[0].terminate()
        processes[0].join(10)
        assert processes[0].exitcode != 0
        time.sleep(1.1)
        fresh = container.source_file_import.claim('recovery')
        assert fresh['id'] == claims[0]['id']
        assert fresh['claim_token'] != claims[0]['claim_token']
        from data_sync_etl.application.source_file_import import SourceImportConflict
        with pytest.raises(SourceImportConflict):
            container.source_file_import.heartbeat(claims[0])
        container.source_file_import.heartbeat(fresh)
    finally:
        if processes[0].is_alive():
            processes[0].terminate()
            processes[0].join(10)


def test_expired_audit_with_live_processing_lock_does_not_spend_retry(container):
    c = container
    c.settings.reconciliation_max_active = 2
    q = AuditQueue(c.uow, c.settings)
    key = q.enqueue('METADATA', 'resources')
    claim = q.claim(key)
    with c.uow() as repo:
        repo.session.get(Job, key).lease_expires_at = now() - timedelta(seconds=1)
    waiting = q.enqueue('METADATA', 'resources')
    with c.uow.processing_lock('reconciliation-resources') as acquired:
        assert acquired
        assert q.claim() is None
    with c.uow() as repo:
        assert repo.session.get(Job, key).attempts == 1
        assert repo.session.get(Job, waiting).attempts == 0
    assert q.claim(key)['claim_token'] != claim['claim_token']


def test_cross_stream_execution_overlaps_and_matches_serial(container, monkeypatch):
    c = container
    c.sync.run(full=True)
    c.settings.reconciliation_max_active = 2
    q = AuditQueue(c.uow, c.settings)
    ids = [q.enqueue('METADATA', stream) for stream in ('resources', 'subjects')]
    barrier = Barrier(2)
    original = ScheduledAudit._metadata

    def overlapping(self, claim):
        barrier.wait(timeout=10)
        return original(self, claim)

    with monkeypatch.context() as patch:
        patch.setattr(ScheduledAudit, '_metadata', overlapping)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda key: ScheduledAudit(c.reconciliation_engine, c.settings).once(key), ids))
    assert results == [True, True]
    with c.uow() as repo:
        rows = [repo.session.get(Job, key) for key in ids]
        assert all(row.status == 'COMPLETED' for row in rows)
        parallel = [{k: row.checkpoint[k] for k in ('records_scanned', 'matched_count', 'mismatched_count')}
                    for row in rows]
    serial = []
    for stream in ('resources', 'subjects'):
        key = q.enqueue('METADATA', stream, mode='FULL')
        assert ScheduledAudit(c.reconciliation_engine, c.settings).once(key)
        with c.uow() as repo:
            row = repo.session.get(Job, key)
            assert row.status == 'COMPLETED'
            serial.append({k: row.checkpoint[k] for k in parallel[0]})
    assert parallel == serial


def test_targeted_repair_waits_for_master_lock_and_preserves_checkpoint(container, monkeypatch):
    c = container
    c.sync.run(full=True)
    with c.uow() as repo:
        before = repo.get('sync_checkpoints', 'subjects')
        subject = repo.find('subjects', limit=1)[0]
        repo.update('subjects', subject['id'], {'subject_name': 'stale'})
    audit = ScheduledAudit(c.reconciliation_engine, c.settings, c.sync)
    key = audit.queue.enqueue('METADATA', 'subjects', mode='FULL', repair=True)
    with c.uow.sync_lock() as acquired:
        assert acquired
        assert audit.once(key)
    with c.uow() as repo:
        row = repo.session.get(Job, key)
        assert row.status == 'RETRY_WAIT'
        assert row.checkpoint['repair_pending'] == [subject['id']]
        row.next_retry_at = now() - timedelta(seconds=1)
        assert repo.get('sync_checkpoints', 'subjects') == before
    reads = []
    original = c.source.selected_batches
    def selected(stream, ids, size):
        reads.extend(ids)
        yield from original(stream, ids, size)
    monkeypatch.setattr(c.source, 'selected_batches', selected)
    assert audit.once(key)
    with c.uow() as repo:
        assert repo.session.get(Job, key).status == 'COMPLETED'
        assert repo.get('sync_checkpoints', 'subjects') == before
        assert repo.get('subjects', subject['id'])['subject_name'] != 'stale'
    assert reads == [subject['id']]


def test_processing_lock_holds_no_open_transaction(container):
    c = container
    owners = []
    def capture(connection, cursor, statement, *args):
        if 'SELECT pg_try_advisory_lock(' in statement:
            owners.append(connection.connection.driver_connection.info.backend_pid)
    event.listen(c.engine, 'after_cursor_execute', capture)
    try:
        with c.uow.processing_lock('transaction-probe') as acquired:
            assert acquired and len(owners) == 1
            with c.engine.connect() as connection:
                state, started = connection.execute(text(
                    'SELECT state, xact_start FROM pg_stat_activity WHERE pid=:pid'),
                    {'pid': owners[0]}).one()
            assert state == 'idle' and started is None
            with c.uow.processing_lock('transaction-probe') as duplicate:
                assert duplicate is False
    finally:
        event.remove(c.engine, 'after_cursor_execute', capture)
    with c.uow.processing_lock('transaction-probe') as acquired:
        assert acquired


def test_readonly_queue_metrics_snapshot(container):
    from scripts.worker_queue_status import snapshot
    seed_imports(container.uow, resources=2)
    result = snapshot(container.engine, container.settings)
    assert result['queues']['source_import']['statuses']['PENDING']['count'] == 2
    assert result['queues']['source_import']['unexpired_active'] == 0
    assert result['queues']['ocr']['expired_active'] == 0
    assert result['postgres']['max_connections'] > 0
    with container.uow() as repo:
        assert repo.count('source_file_imports', {'status': 'PENDING'}) == 2
