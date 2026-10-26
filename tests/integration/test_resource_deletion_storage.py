"""Physical PostgreSQL transactions/concurrency and physical Mongo outbox."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import perf_counter

import pytest
from pymongo import MongoClient
from sqlalchemy import event, inspect

from data_sync_etl.adapters.mongo.metadata_search import MongoMetadataIndex, install
from data_sync_etl.security.auth import AuthError
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_metadata_search import search
from tests.unit.test_resource_soft_delete import ADMIN, deletion
from tests.unit.test_resource_soft_delete import (
    test_local_delete_restore_revision_audit_and_physical_preservation as test_local_delete_restore_revision_audit_and_physical_preservation,
)
from tests.unit.test_resource_soft_delete import (
    test_source_sync_never_changes_local_fence_or_hash_contract as test_source_sync_never_changes_local_fence_or_hash_contract,
)
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_single_admin_auth import csrf, login, make_client
from tests.unit.test_soft_delete_migration import (
    test_additive_upgrade_preserves_rows_and_guarded_rollback as test_additive_upgrade_preserves_rows_and_guarded_rollback,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


def test_concurrent_delete_restore_compare_and_swap(metadata):
    c, _, _, _ = metadata
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: deletion(c), range(4)))
    assert sum(row['changed'] for row in results) == 1
    def restore(_):
        try:
            return deletion(c, restore=True, expected_revision=1)['changed']
        except AuthError as error:
            assert error.code == 'RESOURCE_DELETION_CONFLICT'
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(restore, range(4))) == 1
    with c.uow() as repo:
        assert len(repo.find('resource_deletion_events')) == 2


def test_metadata_write_and_delete_serialize_without_resurrection(metadata):
    from data_sync_etl.domain.core import DomainError
    c, _, _, _ = metadata
    entered, release = Event(), Event()
    def update():
        with c.uow() as repo:
            repo.get_resource_for_update('resource-1')
            entered.set()
            assert release.wait(10)
            repo.update_resource_metadata('resource-1', {'title': 'Synthetic update before delete'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(update)
        assert entered.wait(5)
        delete = pool.submit(deletion, c)
        release.set()
        pending.result(timeout=10)
        assert delete.result(timeout=10)['deleted']
    with pytest.raises(DomainError), c.uow() as repo:
        repo.update_resource_metadata('resource-1', {'title': 'Must not resurrect'})
    with c.uow() as repo:
        resource = repo.get('master_learning_resources', 'resource-1')
        assert resource['master_deleted_at'] and resource['title'] == 'Synthetic update before delete'


def test_batch_sync_and_delete_acquire_epoch_only_after_resource_locks(metadata):
    from data_sync_etl.application.resource_deletion import ResourceDeletion
    c, _, _, _ = metadata
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-2', {'source_origin': 'LOCAL'})
    entered, release = Event(), Event()
    def batch():
        with c.uow() as repo:
            repo.sync_upsert('master_learning_resources', {'id': 'resource-1', 'is_active': False})
            entered.set()
            assert release.wait(10)
            repo.sync_upsert('master_learning_resources', {'id': 'resource-2', 'is_active': False})
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(batch)
        assert entered.wait(5)
        deletion = pool.submit(ResourceDeletion(c.uow).change, 'resource-2', ADMIN)
        try:
            assert deletion.result(timeout=5)['deleted']
        finally:
            release.set()
        pending.result(timeout=10)
    with c.uow() as repo:
        assert repo.get('master_learning_resources', 'resource-2')['master_deleted_at']


def test_real_mongo_propagation_with_old_index_and_durable_pg_event(metadata, mongo_test_database):
    c, version, _, service = metadata
    uri, name = mongo_test_database
    with MongoClient(uri) as client:
        install(client[name])
        service.store = MongoMetadataIndex(client[name])
        assert service.reconcile(apply=True)['writes'] == 1
        deletion(c)
        assert search(c)['total'] == 0  # Mongo still READY; PG must win immediately.
        assert service.propagate_deletions() == 1
        assert service.propagate_deletions() == 0
        row = service.store.collection.find_one({'_id': version['id']})
        assert row['status'] == 'STALE' and row['fields']
        with c.uow() as repo:
            assert repo.find('resource_deletion_events')[0]['propagated_at'] is not None


def test_authenticated_http_delete_restore_on_physical_pg_mongo(metadata, mongo_test_database, configured):
    c, version, _, service = metadata
    uri, name = mongo_test_database
    with MongoClient(uri) as client:
        install(client[name])
        service.store = MongoMetadataIndex(client[name])
        service.reconcile(apply=True)
        with make_client(configured) as api:
            assert login(api, configured[1]).status_code == 200
            headers = csrf(api)
            def query():
                response = api.post('/api/v1/search', headers=headers,
                    json={'query': 'quy đồng', 'method': 'bow'})
                assert response.status_code == 200
                return response.json()
            assert query()['total'] == 1
            deleted = api.request('DELETE', '/api/v1/resources/resource-1', headers=headers,
                                  json={'expected_revision': 0, 'reason': 'Physical QA'})
            assert deleted.status_code == 200 and deleted.json()['revision'] == 1
            assert service.store.collection.find_one({'_id': version['id']})['status'] == 'READY'
            assert query()['items'] == []  # Real stale Mongo record cannot defeat PG.
            assert api.get('/api/v1/resources/resource-1').status_code == 404
            assert api.get(f"/api/v1/resource-versions/{version['id']}/file").status_code == 404
            assert service.propagate_deletions() == 1
            assert api.post('/api/v1/resources/resource-1/restore', headers=headers,
                            json={'expected_revision': 1}).status_code == 200
            assert query()['total'] == 0
            service.reconcile(apply=True)
            assert query()['total'] == 1


def test_physical_delete_restore_latency_and_immediate_search_gate(metadata, mongo_test_database):
    c, _, _, service = metadata
    uri, name = mongo_test_database
    statements = [0]
    def sql(*_):
        statements[0] += 1
    event.listen(c.engine, 'before_cursor_execute', sql)
    measurements = {key: [] for key in ('delete', 'restore', 'search_live', 'search_deleted', 'propagate')}
    round_trips = {key: [] for key in measurements}
    def measure(name, work):
        start, count = perf_counter(), statements[0]
        result = work()
        measurements[name].append(perf_counter() - start)
        round_trips[name].append(statements[0] - count)
        return result
    try:
        with MongoClient(uri) as client:
            install(client[name])
            service.store = MongoMetadataIndex(client[name])
            service.reconcile(apply=True)
            for _ in range(20):
                assert measure('search_live', lambda: search(c))['total'] == 1
                measure('delete', lambda: deletion(c))
                assert measure('search_deleted', lambda: search(c))['total'] == 0
                assert measure('propagate', service.propagate_deletions) >= 1
                measure('restore', lambda: deletion(c, restore=True))
                assert search(c)['total'] == 0
                service.reconcile(apply=True)
    finally:
        event.remove(c.engine, 'before_cursor_execute', sql)
    evidence = os.environ.get('ETL_SOFT_DELETE_EVIDENCE')
    if evidence:
        summary = {key: {'samples': len(values), 'p50_seconds': sorted(values)[len(values)//2],
                        'p95_seconds': sorted(values)[18], 'seconds': values, 'sql_statements': round_trips[key]}
                   for key, values in measurements.items()}
        Path(evidence, 'performance.json').write_text(json.dumps({
            'scope': 'physical isolated PG/Mongo, one synthetic resource, BOW, no HTTP/BGE',
            'search_effect': 'zero hits on first query after each delete commit',
            'index_count': len(inspect(c.engine).get_table_names()), 'measurements': summary}, indent=2), encoding='utf-8')


def test_paired_http_visibility_guard_cost(metadata, configured, monkeypatch):
    from data_sync_etl.api.resource_visibility import ResourceVisibilityBoundary
    c, _, _, service = metadata
    service.reconcile(apply=True)
    original = ResourceVisibilityBoundary.__call__
    async def unguarded(self, scope, receive, send):
        return await self.app(scope, receive, send)
    statement_count = [0]
    def count(*_):
        statement_count[0] += 1
    event.listen(c.engine, 'before_cursor_execute', count)
    results = {}
    try:
        with make_client(configured) as api:
            assert login(api, configured[1]).status_code == 200
            headers = csrf(api)
            expected = None
            for mode in ('before_test_only_guard_bypass', 'after_guard_enabled'):
                monkeypatch.setattr(ResourceVisibilityBoundary, '__call__', unguarded if mode.startswith('before') else original)
                samples, sql_counts = [], []
                for _ in range(20):
                    start, initial = perf_counter(), statement_count[0]
                    response = api.post('/api/v1/search', headers=headers, json={'query': 'quy đồng', 'method': 'bow'})
                    assert response.status_code == 200, response.text
                    body = response.json()
                    assert body['total'] == 1
                    if expected is None:
                        expected = body
                    assert body == expected
                    samples.append(perf_counter() - start)
                    sql_counts.append(statement_count[0] - initial)
                results[mode] = {'samples': samples, 'p50_seconds': sorted(samples)[10],
                    'p95_seconds': sorted(samples)[18], 'sql_statements': sql_counts}
    finally:
        event.remove(c.engine, 'before_cursor_execute', count)
    evidence = os.environ.get('ETL_SOFT_DELETE_EVIDENCE')
    if evidence:
        Path(evidence, 'guard-overhead.json').write_text(json.dumps({
            'scope': 'authenticated ASGI, physical PG, memory metadata store; one synthetic resource; no TCP/TLS/model',
            'response_parity': True, 'measurements': results}, indent=2), encoding='utf-8')
