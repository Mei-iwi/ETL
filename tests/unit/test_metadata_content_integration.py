from types import SimpleNamespace

import pytest

from data_sync_etl.application.metadata_search import MetadataSearchService
from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit import test_search
from tests.unit.test_metadata_search import Embedding, Store

searchable = test_search.searchable


def test_real_content_and_metadata_one_card_with_context(searchable, monkeypatch):
    c, version = searchable
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'title': 'Page one text', 'mongo_id': 'desc'})
    extension = SimpleNamespace(id='desc', description='Page one text', tags=['Page'])
    service = MetadataSearchService(c.uow, SimpleNamespace(get_resource=lambda _: extension), Store(), Embedding(), c.settings)
    c.settings.search_metadata_enabled = c.settings.search_metadata_content_enabled = True
    c.search = SearchService(c.indexing, c.settings, c.indexing.bags, metadata=service)
    def forbidden(*args, **kwargs):
        pytest.fail('Metadata invoked source bytes or OCR')
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    monkeypatch.setattr(c.postprocess, 'run', forbidden)
    assert service.reconcile(apply=True)['ready'] == 1
    result = c.search.search('Page one text', 'bow', access=SearchAccess('local_demo'))
    assert result['total'] == 1
    item = result['items'][0]
    assert item['match_origin'] == 'BOTH' and item['resource_version_id'] == version['id']
    assert item['content_unit_id'] and item['context_id'] and item['context_url']
    assert {'CONTENT', 'TITLE', 'DESCRIPTION'} <= set(item['matched_fields'])
    extension.description = 'updated description'
    assert service.reconcile(apply=True)['writes'] == 1


def test_metadata_outage_keeps_content(searchable):
    c, _ = searchable
    c.settings.search_metadata_enabled = True
    class Unavailable:
        def retrieve(self, *args, **kwargs):
            raise RuntimeError('sensitive driver exception')
    c.search.metadata = Unavailable()
    result = c.search.search('Page one text', 'bow')
    assert result['total'] == 1 and result['branch_status']['metadata'] == 'METADATA_UNAVAILABLE'
    assert 'sensitive' not in str(result)


def test_metadata_candidate_cap_keeps_authorized_content(searchable):
    from data_sync_etl.domain.search import SearchError
    c, _ = searchable
    c.settings.search_metadata_enabled = True
    class Bounded:
        def retrieve(self, *args, **kwargs):
            raise SearchError('METADATA_CANDIDATE_LIMIT', 422)
    c.search.metadata = Bounded()
    result = c.search.search('Page one text', 'bow')
    assert result['total'] == 1
    assert result['branch_status']['metadata'] == 'METADATA_CANDIDATE_LIMIT'


def test_stale_contextual_content_is_explicit_metadata_still_searchable(searchable, monkeypatch):
    from data_sync_etl.domain.search import SearchError
    c, _ = searchable
    c.settings.search_metadata_enabled = True
    service = MetadataSearchService(c.uow, None, Store(), Embedding(), c.settings)
    c.search.metadata = service
    service.reconcile(apply=True)
    def stale(*args, **kwargs):
        raise SearchError('SEMANTIC_INDEX_STALE', 409)
    monkeypatch.setattr(c.search, '_content_search', stale)
    result = c.search.search('Demo Learning Resource', 'bow')
    assert result['total'] == 1
    assert result['branch_status']['content'] == 'SEMANTIC_INDEX_STALE'
    assert result['items'][0]['context_id'] is None
