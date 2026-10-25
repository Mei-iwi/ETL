"""Refresh checkpoints and advisory locks on an isolated PostgreSQL schema."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from tests.unit.test_metadata_master_refresh import (
    test_idle_existing_worker_refreshes_metadata_after_commit as test_idle_existing_worker_refreshes_metadata_after_commit,
)
from tests.unit.test_metadata_master_refresh import (
    test_retry_preserves_cursor_and_replays_failed_resource as test_retry_preserves_cursor_and_replays_failed_resource,
)
from tests.unit.test_metadata_search import metadata as metadata

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


def test_two_metadata_sweep_owners_and_storage_diagnostics(metadata, monkeypatch):
    from data_sync_etl.application.metadata_refresh import MetadataRefresh
    c, _, _, service = metadata
    c.settings.search_metadata_auto_refresh_enabled = True
    entered, release = Event(), Event()
    original = service.reconcile
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(service, 'reconcile', blocked)
    first, second = MetadataRefresh(service, c.settings), MetadataRefresh(service, c.settings)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(first.tick)
        try:
            assert entered.wait(5)
            assert second.tick()['status'] == 'BUSY'
        finally:
            release.set()
        report = pending.result(timeout=10)
    assert report['status'] == 'READY' and report['writes'] == 1 and report['sweep_complete']
    service.store.status_counts = lambda: {'lexical': {'READY': 1}, 'dense': {'NOT_INDEXED': 1}, 'scope': 'INDEX_STORAGE_NOT_AUTHORITY_OR_ACL'}
    diagnostics = service.diagnostics()
    assert diagnostics['checkpoint']['last_completed_sweep_at']
    assert not diagnostics['checkpoint']['has_cursor']
    assert 'resource-1' not in str(diagnostics) and 'desc' not in str(diagnostics)


def test_crash_between_index_write_and_pg_checkpoint_replays_idempotently(metadata, monkeypatch):
    from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyRepository
    from data_sync_etl.application.metadata_refresh import CHECKPOINT, MetadataRefresh
    c, version, _, service = metadata
    c.settings.search_metadata_auto_refresh_enabled = True
    c.settings.search_metadata_refresh_batch_size = 1
    update = SQLAlchemyRepository.update
    failed = [False]
    def fault(repo, table, key, values):
        if table == 'sync_checkpoints' and key == CHECKPOINT and values.get('last_id') and not failed[0]:
            failed[0] = True
            raise RuntimeError('synthetic crash after Mongo write')
        return update(repo, table, key, values)
    monkeypatch.setattr(SQLAlchemyRepository, 'update', fault)
    job = MetadataRefresh(service, c.settings)
    assert job.tick()['status'] == 'RETRY_REQUIRED'
    assert version['id'] in service.store.rows
    with c.uow() as repo:
        assert repo.get('sync_checkpoints', CHECKPOINT)['last_id'] is None
    job.next_due = 0
    result = job.tick()
    assert result['status'] == 'READY' and result['writes'] == 0 and len(service.store.rows) == 1


@pytest.mark.parametrize('count', [201, 2000, 10000])
def test_large_registry_sweep_wraparound_and_late_smaller_key(metadata, count):
    """Real PG checkpoints, fake reconcile work: not an embedding throughput test."""
    from collections import Counter
    from types import SimpleNamespace

    from data_sync_etl.application.metadata_refresh import MetadataRefresh
    from tests.unit.test_metadata_indexed_candidates import seed
    c, _, _, service = metadata
    seed(c, count)
    seen = Counter()
    def ready(filters, **kwargs):
        seen[filters['resource_id']] += 1
        return {'status': 'READY', 'writes': 0, 'embeds': 0}
    c.settings.search_metadata_auto_refresh_enabled = True
    c.settings.search_metadata_refresh_batch_size = 50
    fake = SimpleNamespace(uow=c.uow, store=SimpleNamespace(ensure_ready=lambda: None), reconcile=ready)
    job = MetadataRefresh(fake, c.settings)
    assert job.tick()['processed'] == 50
    with c.uow() as repo:
        template = repo.get('master_learning_resources', 'resource-1')
        repo.insert('master_learning_resources', {**template, 'id': '000-late-smaller', 'mongo_id': None})
    for _ in range((count + 2) // 50 + 3):
        job.next_due = 0
        report = job.tick()
        if report.get('sweep_complete'):
            break
    else:
        pytest.fail('Bounded sweep did not wrap')
    assert '000-late-smaller' not in seen and len(seen) == count + 2
    assert set(seen.values()) == {1}
    restarted = MetadataRefresh(fake, c.settings)
    assert restarted.tick()['status'] == 'READY'
    assert seen['000-late-smaller'] == 1
