"""File/OCR/index deletion races; actual pipeline with isolated encoder doubles."""
from copy import deepcopy

import pytest

from data_sync_etl.application.resource_deletion import ResourceDeletion
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.ports.processing import OCRResult
from tests.conftest import ingest
from tests.unit.test_resource_soft_delete import ADMIN, deletion
from tests.unit.test_search import searchable as searchable
from tests.unit.test_semantic import semantic_search as semantic_search
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_single_admin_auth import csrf, login, make_client
from tests.unit.test_source_file_import import importing as importing


def test_pending_and_inflight_ocr_pause_without_failing_ready_content(container):
    c = container
    version = ingest(c, ['Synthetic native PDF'])
    c.ocr.create_job(version['id'])
    deletion(c)
    assert c.ocr.claim('deleted-worker') is None
    deletion(c, restore=True)
    claimed = c.ocr.claim('live-worker')
    assert claimed
    deletion(c)
    assert not c.ocr.succeed(claimed, OCRResult('Late text', engine_name='test'))
    with c.uow() as repo:
        assert not repo.find('ocr_page_results')
        assert repo.get('ocr_page_tasks', claimed['id'])['status'] == 'PENDING'
    deletion(c, restore=True)
    claimed = c.ocr.claim('restored-worker')
    result, image = c.ocr.recognize(claimed)
    assert c.ocr.succeed(claimed, result, image)


def test_ocr_aba_delete_restore_rejects_old_claim(container):
    c = container
    version = ingest(c, ['Synthetic native PDF'])
    c.ocr.create_job(version['id'])
    claim = c.ocr.claim('old-worker')
    deletion(c)
    deletion(c, restore=True)
    assert not c.ocr.succeed(claim, OCRResult('Stale claim', engine_name='test'))


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_content_search_context_and_file_hide_deleted_resource(searchable, method):
    c, version = searchable
    hit = c.search.search('text', method)['items'][0]
    before_units = deepcopy(c.documents.units)
    before_bags = deepcopy(c.indexing.bags.rows)
    deletion(c)
    assert c.search.search('text', method)['items'] == []
    with pytest.raises(SearchError):
        c.search.context(hit['context_id'])
    with pytest.raises(SearchError):
        c.search.file_version(version['id'])
    assert c.documents.units == before_units and c.indexing.bags.rows == before_bags
    with c.uow() as repo:
        assert repo.find('content_units', {'resource_version_id': version['id']})
    deletion(c, restore=True)
    assert c.search.search('text', method)['items'] == []  # Generation fenced.
    c.indexing.enqueue(version['id'], force=True)
    assert c.indexing.once()
    assert c.search.search('text', method)['items']


def test_authenticated_api_blocks_all_versions_preview_metadata_context(configured, searchable):
    c, version = searchable
    # configured and searchable share the same isolated container fixture.
    c.settings = configured[0].settings
    hit = c.search.search('text', 'bow')['items'][0]
    with make_client(configured) as api:
        assert api.request('DELETE', '/api/v1/resources/resource-1').status_code == 401
        assert login(api, configured[1]).status_code == 200
        assert api.request('DELETE', '/api/v1/resources/resource-1').status_code == 403  # CSRF
        response = api.request('DELETE', '/api/v1/resources/resource-1', headers=csrf(api), json={'reason': 'QA'})
        assert response.status_code == 200 and response.json()['revision'] == 1
        paths = ['/api/v1/resources/resource-1', '/api/v1/resources/resource-1/versions',
                 '/api/v1/resources/resource-1/description',
                 f"/api/v1/chunks/{hit['context_id']}/context"]
        paths += [f"/api/v1/resource-versions/{version['id']}/{suffix}" for suffix in
                  ('file', 'source-info', 'chunks', 'content-units', 'index-status', 'etl-status')]
        for path in paths:
            assert api.get(path).status_code == 404, path
        assert api.post('/api/v1/resources/resource-1/restore', headers=csrf(api),
                        json={'expected_revision': 0}).status_code == 409
        assert api.post('/api/v1/resources/resource-1/restore', headers=csrf(api),
                        json={'expected_revision': 1}).status_code == 200
        assert api.get('/api/v1/resources/resource-1').status_code == 200


def test_semantic_late_encoder_cannot_publish_deleted_version(semantic_search, monkeypatch):
    c, version, semantic = semantic_search
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'source_origin': 'LOCAL'})
    encode = semantic.embedding.embed_documents
    def late(texts):
        result = encode(texts)
        deletion(c)
        return result
    monkeypatch.setattr(semantic.embedding, 'embed_documents', late)
    with pytest.raises(IndexingError):
        semantic.build(version['id'], apply=True)
    with pytest.raises(IndexingError):
        semantic.status(version['id'])


def test_deleted_resource_cannot_start_postprocess_or_projection(searchable):
    c, version = searchable
    with c.uow() as repo:
        job = repo.find('ocr_jobs', {'resource_version_id': version['id']})[0]
    before = deepcopy(c.documents.units)
    ResourceDeletion(c.uow).change('resource-1', ADMIN)
    with pytest.raises(DomainError):
        c.postprocess.run(version['id'])
    with pytest.raises(DomainError):
        c.mongo_projector.project(version['id'], job['id'])
    assert c.documents.units == before


def test_delete_during_resource_detail_is_revalidated_at_http_send(configured, searchable, monkeypatch):
    c, _ = searchable
    original = c.resource_detail.run
    def deleted(resource_id):
        result = original(resource_id)
        deletion(c)
        return result
    monkeypatch.setattr(c.resource_detail, 'run', deleted)
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        response = api.get('/api/v1/resources/resource-1')
        assert response.status_code == 404
        assert 'resource_type' not in response.text


def test_global_list_response_epoch_rejects_delete_race(configured, searchable, monkeypatch):
    c, _ = searchable
    listing = c.resource_listing.run
    def deleted(**kwargs):
        result = listing(**kwargs)
        deletion(c)
        return result
    monkeypatch.setattr(c.resource_listing, 'run', deleted)
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        response = api.get('/api/v1/resources?page=1&size=20&q=')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'RESOURCE_VISIBILITY_CHANGED'
        assert 'resource-1' not in response.text


def test_existing_stream_stops_after_deletion_without_sending_next_bytes(searchable):
    import asyncio
    from types import SimpleNamespace

    from data_sync_etl.api.resource_visibility import ResourceVisibilityBoundary
    c, version = searchable
    sent = []
    async def producer(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'already-sent', 'more_body': True})
        deletion(c)
        await send({'type': 'http.response.body', 'body': b'forbidden-late-bytes', 'more_body': False})
    async def receive():
        return {'type': 'http.request', 'body': b''}
    async def send(message):
        sent.append(message)
    scope = {'type': 'http', 'method': 'GET', 'path': f"/api/v1/resource-versions/{version['id']}/file",
             'headers': [], 'query_string': b'', 'app': SimpleNamespace(state=SimpleNamespace(container=c))}
    asyncio.run(ResourceVisibilityBoundary(producer)(scope, receive, send))
    assert [message.get('body') for message in sent if message['type'] == 'http.response.body'] == [b'already-sent', b'']


def test_source_import_claim_pauses_without_consuming_retry_on_deletion(importing):
    c, _, _ = importing
    c.source_file_import.discover_once()
    claimed = c.source_file_import.claim()
    assert claimed is None
    deletion(c)
    with c.uow() as repo:
        row, = repo.find('source_file_imports', {'resource_id': 'resource-1'})
        assert row['status'] == 'METADATA_ONLY' and row['attempts'] == 0
        assert repo.find('resource_versions', {'resource_id': 'resource-1'})
        assert not repo.find('ocr_jobs', {})
    with pytest.raises(Exception, match='FILE_NOT_FOUND'):
        c.source_resolver.resolve(row['target_version_id'])
    assert c.source_file_import.claim() is None


def test_delete_during_bag_embedding_does_not_publish_or_fail_content(searchable, monkeypatch):
    from data_sync_etl.application.processing_state import processing_state
    c, version = searchable
    embed = c.indexing.embedding.embed
    def late(text):
        result = embed(text)
        deletion(c)
        return result
    monkeypatch.setattr(c.indexing.embedding, 'embed', late)
    c.indexing.enqueue(version['id'], force=True)
    assert c.indexing.once()
    assert c.search.search('text', 'hybrid')['items'] == []
    with c.uow() as repo:
        assert repo.find('etl_stage_runs', {'stage': 'INDEX_BUILD'}, order=('-created_at', '-id'))[0]['status'] == 'FAILED'
        job = repo.find('ocr_jobs', {'resource_version_id': version['id']})[0]
        assert processing_state(repo, job) == 'CONTENT_READY'


def test_pending_bag_build_does_not_start_for_deleted_resource(searchable):
    c, version = searchable
    c.indexing.enqueue(version['id'], force=True)
    deletion(c)
    assert not c.indexing.once()
    assert not c.indexing.once(version['id'])
    with c.uow() as repo:
        assert repo.find('etl_stage_runs', {'stage': 'INDEX_BUILD'}, order=('-created_at', '-id'))[0]['status'] == 'PENDING'


def test_source_tombstone_during_http_list_also_changes_epoch(configured, searchable, monkeypatch):
    from data_sync_etl.domain.core import now
    c, _ = searchable
    listing = c.resource_listing.run
    def deleted(**kwargs):
        result = listing(**kwargs)
        with c.uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'deleted_at': now()})
        return result
    monkeypatch.setattr(c.resource_listing, 'run', deleted)
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        response = api.get('/api/v1/resources?page=1&size=20&q=')
        assert response.status_code == 409 and 'resource-1' not in response.text
