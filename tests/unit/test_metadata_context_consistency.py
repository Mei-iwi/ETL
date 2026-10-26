"""Real input dependencies; embeddings doubled, no OCR/re-extraction permitted."""
from copy import deepcopy

import pytest

from data_sync_etl.application.metadata_refresh import MetadataRefresh
from data_sync_etl.application.metadata_search import MetadataSearchService
from tests.unit.test_metadata_search import Embedding, Store
from tests.unit.test_search import searchable as searchable
from tests.unit.test_semantic import semantic_search as semantic_search


def configure(c, semantic):
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'source_origin': 'LOCAL'})
    service = MetadataSearchService(c.uow, None, Store(), Embedding(), c.settings)
    c.search.metadata = service
    c.settings.search_metadata_enabled = c.settings.search_metadata_content_enabled = True
    c.settings.search_metadata_auto_refresh_enabled = c.settings.search_metadata_refresh_content_semantic = True
    c.settings.semantic_enabled = True
    return service, MetadataRefresh(service, c.settings, semantic)


def test_context_title_change_stale_then_selectively_reembedded_without_ocr(semantic_search, monkeypatch):
    c, version, semantic = semantic_search
    semantic.build(version['id'], apply=True)
    previous = deepcopy(c.documents.units)
    metadata, job = configure(c, semantic)
    def forbidden(*args, **kwargs):
        pytest.fail('Metadata context refresh re-extracted file or OCR')
    monkeypatch.setattr(c.process_version, 'run', forbidden)
    monkeypatch.setattr(c.source_resolver, 'temporary_path', forbidden)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Nhiệt học mới'})
    assert semantic.status(version['id'])['status'] == 'STALE'
    assert job.tick()['status'] == 'READY'
    assert semantic.status(version['id'])['status'] == 'READY'
    assert c.documents.units == previous
    calls = semantic.embedding.calls
    job.next_due = 0
    assert job.tick()['status'] == 'READY'
    assert semantic.embedding.calls == calls
    assert metadata.store.rows[version['id']]['status'] == 'READY'


def test_failed_context_reembed_keeps_metadata_and_lexical_available(semantic_search):
    c, version, semantic = semantic_search
    semantic.build(version['id'], apply=True)
    _, job = configure(c, semantic)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Changed title'})
    semantic.embedding.fail_after = semantic.embedding.calls
    assert job.tick()['status'] == 'RETRY_REQUIRED'
    assert c.search.search('Page one text', 'hybrid')['total'] > 0
    result = c.search.search('Page one text', 'hybrid')
    assert result['method'] == 'hybrid'
    assert result['branch_status']['content'] == 'PARTIAL'
    assert result['branch_status']['content_dense'] in ('SEMANTIC_INDEX_STALE', 'SEMANTIC_INDEX_NOT_READY', 'SEMANTIC_INFERENCE_FAILED')
    semantic.embedding.fail_after = None
    job.next_due = 0
    assert job.tick()['status'] == 'READY'


def test_input_identity_ignores_metadata_not_used_by_raw_template(semantic_search):
    c, version, semantic = semantic_search
    c.settings.semantic_input_identity_enabled = True
    c.settings.semantic_strategy = 'raw'
    old = semantic.build(version['id'], apply=True)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Different irrelevant raw title'})
    new = semantic.build(version['id'], apply=True)
    assert new['index_id'] == old['index_id'] and new['action'] == 'UNCHANGED'


@pytest.mark.parametrize('field', ['title', 'subjects', 'grades'])
def test_context_dependencies_change_plan_but_description_and_author_do_not(semantic_search, field):
    c, version, semantic = semantic_search
    c.settings.semantic_input_identity_enabled = True
    snapshot, metadata = semantic._snapshot(version['id'])
    old = semantic.plan(snapshot, metadata)[0]
    changed = {**metadata, field: 'Changed title' if field == 'title' else ['Changed catalog']}
    assert semantic.plan(snapshot, changed)[0]['_id'] != old['_id']
    assert semantic.plan(snapshot, {**metadata, 'description': 'Not in template', 'authors': ['PRIVATE']})[0]['_id'] == old['_id']
