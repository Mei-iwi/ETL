"""Committed MASTER → bounded refresh → authorized search, without file/OCR I/O."""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from data_sync_etl.application.metadata_refresh import CHECKPOINT, MetadataRefresh
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.workers.master_sync import MasterSyncWorker
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_metadata_search import search


def refresh(c, service):
    c.settings.search_metadata_auto_refresh_enabled = True
    c.settings.search_metadata_refresh_dense = True
    c.settings.search_metadata_refresh_batch_size = 1
    job = MetadataRefresh(service, c.settings)
    return job


def test_missing_index_has_explicit_readiness(metadata):
    c, _, _, _ = metadata
    result = search(c)
    assert result['total'] == 0
    assert result['branch_status']['metadata_lexical'] == 'NOT_READY'


def test_idle_existing_worker_refreshes_metadata_after_commit(metadata, monkeypatch):
    c, version, extension, service = metadata
    def forbidden(*args, **kwargs):
        pytest.fail('Refresh invoked media bytes, OCR or content reindex')
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    monkeypatch.setattr(c.postprocess, 'run', forbidden)
    monkeypatch.setattr(c.indexing, 'enqueue', forbidden)
    job = refresh(c, service)
    worker = MasterSyncWorker(c.sync, SimpleNamespace(heartbeat_and_claim=lambda _: []), metadata_refresh=job)
    assert worker.once('fixture') == {'action': 'IDLE'}
    assert search(c)['total'] == 1
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Định luật bảo toàn năng lượng'})
    extension.description = 'Động năng và thế năng'
    extension.tags = ['cơ học']
    assert search(c)['total'] == 0  # Changed authority is rejected before refresh.
    job.next_due = 0
    # Restarted coordinator keeps the durable cursor; finish sweep, then restart.
    worker.once('fixture')
    job = refresh(c, service)
    for _ in range(4):
        job.next_due = 0
        job.tick()
    assert search(c, 'quy đồng')['total'] == 0
    assert search(c, 'động năng')['total'] == 1
    assert search(c, 'bảo toàn')['items'][0]['matched_fields'] == ['TITLE']
    with c.uow() as repo:
        assert not repo.find('content_units', {'resource_version_id': version['id']})


def test_model_revision_change_invalidates_old_dense_even_lexical_only(metadata):
    _, version, _, service = metadata
    service.reconcile(apply=True, dense=True)
    service.embedding.model_identity = lambda: {'model_id': 'BAAI/bge-m3', 'revision': 'new', 'dimension': 1024}
    result = service.reconcile(apply=True)
    assert result['writes'] == 1
    assert service.store.rows[version['id']]['dense_status'] == 'NOT_INDEXED'


def test_retry_preserves_cursor_and_replays_failed_resource(metadata, monkeypatch):
    c, _, _, service = metadata
    job = refresh(c, service)
    original = service.store.upsert
    monkeypatch.setattr(service.store, 'upsert', lambda _: (_ for _ in ()).throw(RuntimeError('fixture failure')))
    assert job.tick()['status'] == 'RETRY_REQUIRED'
    with c.uow() as repo:
        assert repo.get('sync_checkpoints', CHECKPOINT)['last_id'] is None
    monkeypatch.setattr(service.store, 'upsert', original)
    assert job.tick()['status'] == 'DISABLED_OR_WAITING'
    job.next_due = 0
    assert job.tick()['processed'] == 1
    assert search(c)['total'] == 1


def test_disabled_refresh_performs_no_writes(metadata):
    c, _, _, service = metadata
    assert MetadataRefresh(service, c.settings).tick()['status'] == 'DISABLED_OR_WAITING'
    assert not service.store.rows
    with c.uow() as repo:
        assert repo.get('sync_checkpoints', CHECKPOINT) is None


def test_reconcile_writer_busy_has_no_write(metadata, monkeypatch):
    _, _, _, service = metadata
    @contextmanager
    def busy(_):
        yield False
    monkeypatch.setattr(service.uow, 'metadata_projection_lock', busy)
    with pytest.raises(IndexingError, match='METADATA_INDEX_BUSY'):
        service.reconcile(apply=True)
    assert not service.store.rows


def test_dense_evidence_does_not_claim_description_attribution(metadata):
    c, _, _, service = metadata
    service.reconcile(apply=True, dense=True)
    item = search(c, 'query without lexical overlap', method='vector')['items'][0]
    assert item['matched_fields'] == [] and item['match_origin'] == 'METADATA'
    assert item['context_id'] is None


def test_author_only_edit_reuses_dense_but_removed_description_disappears(metadata):
    c, version, extension, service = metadata
    service.reconcile(apply=True, dense=True)
    extension.authors = [SimpleNamespace(name='Private author')]
    assert service.reconcile(apply=True, dense=True)['unchanged'] == 1
    extension.description = None
    extension.tags = []
    assert search(c)['total'] == 0
    assert service.reconcile(apply=True, dense=True)['embeds'] == 1
    assert 'description' not in service.store.rows[version['id']]['fields']
    assert search(c, 'quy đồng')['total'] == 0


def test_refresh_failure_after_commit_is_recoverable_without_ocr(metadata, monkeypatch):
    c, _, _, service = metadata
    job = refresh(c, service)
    original = service.store.ensure_ready
    monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(RuntimeError('fixture outage')))
    assert job.tick()['status'] == 'RETRY_REQUIRED'
    with c.uow() as repo:
        assert repo.get('sync_checkpoints', CHECKPOINT) is None
    monkeypatch.setattr(service.store, 'ensure_ready', original)
    job.next_due = 0
    assert job.tick()['status'] == 'READY'


def test_source_fixture_change_is_invisible_until_master_commit(metadata):
    c, _, _, service = metadata
    service.reconcile(apply=True)
    path = c.source.root / 'resources.json'
    rows = json.loads(path.read_text(encoding='utf-8'))
    next(row for row in rows if row['id'] == 'resource-1')['title'] = 'Nhiệt động lực học'
    path.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
    assert search(c, 'nhiệt động')['total'] == 0
    assert search(c, 'cộng hai phân số')['total'] == 1
    c.sync.run('resources', full=True)
    assert search(c, 'cộng hai phân số')['total'] == 0
    job = refresh(c, service)
    assert job.tick()['status'] == 'READY'
    assert search(c, 'nhiệt động')['total'] == 1


def test_master_patch_and_grade_visibility_refresh_without_content(metadata):
    from data_sync_etl.domain.resource_metadata import UpdateResourceMetadataCommand
    c, _, _, service = metadata
    service.reconcile(apply=True)
    c.update_resource_metadata.execute(UpdateResourceMetadataCommand(
        resource_id='resource-1', title='Sóng điện từ', supplied_fields=frozenset({'title'})))
    assert search(c)['total'] == 0
    assert refresh(c, service).tick()['status'] == 'READY'
    assert search(c, 'sóng điện')['total'] == 1
    assert search(c, 'sóng điện', filters={'grade_level_id': 'grade-1'})['total'] == 1
    with c.uow() as repo:
        repo.update('master_grade_levels', 'grade-1', {'education_is_active': False})
    assert search(c, 'sóng điện', filters={'grade_level_id': 'grade-1'})['total'] == 0


def test_corpus_above_200_is_bounded_and_filter_can_recover(metadata):
    c, _, _, service = metadata
    with c.uow() as repo:
        template = repo.get('master_learning_resources', 'resource-1')
        for ordinal in range(200):
            resource_id = f'metadata-cap-{ordinal:03d}'
            repo.insert('master_learning_resources', {**template, 'id': resource_id, 'mongo_id': None})
            repo.insert('resource_versions', {'resource_id': resource_id, 'version_number': 1,
                'lifecycle_status': 'INGESTED', 'storage_bucket': 'test',
                'storage_object_key': 'registered.mp4', 'source_hash': 'a' * 64})
    result = search(c)
    assert result['total'] == 0 and result['branch_status']['metadata'] == 'METADATA_CANDIDATE_LIMIT'
    assert c.settings.search_metadata_max_resources == 200
    assert service.reconcile({'resource_id': 'resource-1'}, apply=True)['writes'] == 1
    assert search(c, filters={'resource_id': 'resource-1'})['total'] == 1
