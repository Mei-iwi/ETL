"""Real SQLite sync/search/resolution/outbox; explicit fake SOURCE and embedding."""

from copy import deepcopy

import pytest

from data_sync_etl.adapters.source.writeback import InMemorySourceMetadataWriter
from data_sync_etl.application.metadata_search import MetadataSearchService
from data_sync_etl.domain.core import now
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.domain.source_ownership import state_hash
from tests.unit.test_metadata_indexed_candidates import IndexedStore
from tests.unit.test_metadata_search import Embedding


def test_source_search_resolution_writeback_resync_and_reconcile(container, monkeypatch):
    c = container
    c.source.source_origin = 'POSTGRESQL'
    c.sync.run(full=True)
    aggregates = deepcopy([item for batch in c.source.batches('resources') for item in batch])
    original_batches = c.source.batches
    def batches(stream, cursor=None, batch_size=100):
        if stream != 'resources':
            yield from original_batches(stream, cursor, batch_size)
            return
        selected = sorted((item for item in aggregates if cursor is None or item.cursor.key() > cursor.key()),
                          key=lambda item: item.cursor.key())
        for start in range(0, len(selected), batch_size):
            yield selected[start:start + batch_size]
    monkeypatch.setattr(c.source, 'batches', batches)
    source_version = dict(source_version_id='pipeline-v1', resource_id='resource-1',
        resource_title='Demo Learning Resource 1', source_version_number=1,
        parent_version_id=None, source_hash='a' * 64, source_lifecycle_status='APPROVED',
        resource_active=True, resource_deleted_at=None, resource_publication_status='PUBLISHED',
        updated_at=now(), storage_bucket='private', storage_object_key='hidden/private.pdf')
    monkeypatch.setattr(c.source, 'version_page', lambda **_: {'items': [source_version], 'next_cursor': None}, raising=False)
    monkeypatch.setattr(c.source, 'get_version', lambda key: source_version if key == 'pipeline-v1' else None, raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail('Metadata pipeline must not read SOURCE bytes')
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    c.source_file_import.discover_once()
    with c.uow() as repo:
        before = repo.get('master_learning_resources', 'resource-1')
        count = repo.count('master_learning_resources', {})
    access = SearchAccess('authenticated_user', allow_public=True, allow_shared_unreviewed=True)
    c.settings.search_metadata_enabled = c.settings.search_metadata_indexed_candidates_enabled = True
    c.settings.search_metadata_content_enabled = False
    service = MetadataSearchService(c.uow, None, IndexedStore(), Embedding(), c.settings)
    c.search.metadata = service
    assert service.reconcile(apply=True)['writes'] == 1
    result = c.search.search('Demo', 'bow', access=access)
    assert result['total'] == 1 and result['items'][0]['resource_id'] == 'resource-1'
    resolution = c.source_resolution.resolve('resource-1', access, result['items'][0]['resource_version_id'])
    assert resolution['source_record_available'] and resolution['content_availability'] == 'metadata-only'

    class CanonicalFakeWriter(InMemorySourceMetadataWriter):
        def write_metadata(self, **kwargs):
            result = super().write_metadata(**kwargs)
            target = next(item for item in aggregates if item.cursor.id == 'resource-1')
            if target.values['title'] != kwargs['fields']['title']:
                stamp = now()
                target.values.update(title=kwargs['fields']['title'], updated_at=stamp)
                target.cursor = target.cursor.model_copy(update={'updated_at': stamp})
                self.records['resource-1']['source_state_hash'] = state_hash({**before, **target.values})
            return {**result, 'source_state_hash': self.records['resource-1']['source_state_hash']}

    writer = CanonicalFakeWriter({'resource-1': before}, backend='json-fixture')
    c.source_writeback.writer = writer
    c.source_writeback.permission = lambda **grant: grant['actor_id'] == 'fixture-admin'
    c.settings.source_metadata_writeback_enabled = True
    command = dict(expected_source_state_hash=before['source_state_hash'], idempotency_key='pipeline-idempotency',
                   actor_id='fixture-admin', role='ADMIN')
    assert c.source_writeback.request('resource-1', {'title': 'New sentinel title'}, **command)['status'] == 'WRITEBACK_PENDING_APPROVAL'
    assert c.source_writeback.request('resource-1', {'title': 'New sentinel title'}, approve=True, **command)['status'] == 'WRITEBACK_APPLIED'
    assert c.sync.run('resources')['resources']['updated'] == 1
    c.sync.run('resources', full=True)
    with c.uow() as repo:
        current = repo.get('master_learning_resources', 'resource-1')
        assert current['title'] == 'New sentinel title'
        assert current['source_identity_hash'] == before['source_identity_hash']
        assert repo.count('master_learning_resources', {}) == count
        assert repo.count('reconciliation_audit_jobs', {'audit_type': 'SOURCE_WRITEBACK'}) == 1
    service.reconcile(apply=True)
    assert c.search.search('sentinel', 'bow', access=access)['total'] == 1
    assert c.source_writeback.request('resource-1', {'title': 'New sentinel title'}, approve=True, **command)['status'] == 'WRITEBACK_APPLIED'
    report = c.reconciliation_engine.execute_stream_reconcile(stream_name='resources', dry_run=True, mode='FULL')
    assert report['reports'][0]['mismatched_count'] == 0
