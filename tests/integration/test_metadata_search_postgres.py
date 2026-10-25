from types import SimpleNamespace

import pytest

from data_sync_etl.application.metadata_search import MetadataSearchService
from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_metadata_search import Embedding, Store


@pytest.mark.postgres
def test_postgres_current_version_acl_and_metadata_only(pg_container):
    c = pg_container
    c.sync.run(full=True)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {
            'source_origin': 'LOCAL', 'title': 'Cộng hai phân số', 'mongo_id': 'desc'})
        version = repo.insert('resource_versions', {'resource_id': 'resource-1', 'version_number': 1,
            'lifecycle_status': 'INGESTED', 'storage_bucket': 'test', 'storage_object_key': 'registered.mp4', 'source_hash': 'a' * 64})
    extension = SimpleNamespace(id='desc', description='Video quy đồng mẫu số', tags=['phân số'])
    service = MetadataSearchService(c.uow, SimpleNamespace(get_resource=lambda _: extension), Store(), Embedding(), c.settings)
    c.settings.search_metadata_enabled, c.settings.search_metadata_content_enabled = True, False
    search = SearchService(c.indexing, c.settings, None, metadata=service)
    assert service.reconcile(apply=True)['ready'] == 1
    result = search.search('quy đồng', 'bow', access=SearchAccess('authenticated_admin'))
    assert result['total'] == 1 and result['items'][0]['resource_version_id'] == version['id']
    assert search.search('quy đồng', 'bow', access=SearchAccess('authenticated_user', owner_id='other'))['total'] == 0
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'publication_status': 'UNPUBLISHED'})
    assert search.search('quy đồng', 'bow', access=SearchAccess('authenticated_admin'))['total'] == 0
