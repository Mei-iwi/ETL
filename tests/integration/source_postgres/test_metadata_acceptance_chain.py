"""Isolated physical PG/Mongo + authenticated ASGI simulation, NOT real staging.

SOURCE writes below are synthetic fixture setup/updates, never application DBs.
No model/credential download; the dense double is explicitly not BGE inference.
"""
import pytest
from sqlalchemy import text

from data_sync_etl.adapters.mongo.metadata_search import MongoMetadataIndex, install
from data_sync_etl.application.metadata_refresh import MetadataRefresh
from data_sync_etl.application.metadata_search import MetadataSearchService
from data_sync_etl.application.search import SearchService
from tests.unit.test_metadata_search import Embedding
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_single_admin_auth import csrf, login, make_client

from .test_dual_source_metadata_etl import dual as dual
from .test_file_import_flow import setup_import

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


def test_committed_source_master_extension_refresh_auth_search_without_reextraction(
        source, pg_container, dual, configured, tmp_path, monkeypatch):
    c = pg_container
    _, destination, _, master, documents, _ = dual
    setup_import(source, c, tmp_path)
    master.run(full=True)
    c.source_resolver.source = source.adapter
    c.source_resolver.settings.source_reference_enabled = True
    c.source_resolver.settings.source_temporary_files_enabled = True
    c.source_file_import.settings.source_reference_enabled = True
    c.source_file_import.discover_once()
    claimed = c.source_file_import.claim()
    assert claimed is not None
    c.source_file_import.execute(claimed)
    with c.uow() as repo:
        registered = repo.get('source_file_imports', claimed['id'])
        assert registered['target_version_id']
        version_id = registered['target_version_id']
        assert not repo.find('content_units', {'resource_version_id': version_id})
    install(destination, candidates=True)
    service = MetadataSearchService(c.uow, documents, MongoMetadataIndex(destination), Embedding(), c.settings)
    c.metadata_documents = documents
    c.settings.search_metadata_enabled = c.settings.search_metadata_indexed_candidates_enabled = True
    c.settings.search_metadata_content_enabled = False
    c.settings.search_metadata_auto_refresh_enabled = True
    c.search = SearchService(c.indexing, c.settings, None, metadata=service)
    job = MetadataRefresh(service, c.settings)
    def no_bytes(*args, **kwargs):
        pytest.fail('Metadata sync/index/search re-read file bytes or OCR')
    monkeypatch.setattr(c.source_files, 'open_stream', no_bytes)
    monkeypatch.setattr(c.process_version, 'run', no_bytes)
    assert job.tick()['status'] == 'READY'
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        headers = csrf(api)
        def query(value):
            response = api.post('/api/v1/search', headers=headers, json={'query': value, 'method': 'bow'})
            assert response.status_code == 200, response.text
            return response.json()
        assert query('Source description')['total'] == 1
        with source.engine.begin() as connection:
            connection.execute(text("UPDATE learning_resources SET title='Synthetic thermal lesson',updated_at=now() WHERE id='resource-1'"))
        assert query('thermal')['total'] == 0  # Never consult SOURCE directly.
        master.run('resources', full=True)
        assert query('thermal')['total'] == 0  # Index safely stale until refresh.
        job.next_due = 0
        assert job.tick()['status'] == 'READY'
        assert query('thermal')['items'][0]['resource_version_id'] == version_id
        current = api.get('/api/v1/resources/resource-1/description').json()
        response = api.patch('/api/v1/resources/resource-1/description', headers=headers,
            json={'revision': current['revision'], 'description': 'Conduction in solid materials', 'tags': ['conduction']})
        assert response.status_code == 200, response.text
        assert query('conduction')['total'] == 0
        job.next_due = 0
        assert job.tick()['status'] == 'READY'
        assert query('conduction')['items'][0]['matched_fields']
        assert query('Source description')['total'] == 0
        with source.engine.begin() as connection:
            connection.execute(text("UPDATE learning_resources SET publication_status='DRAFT',updated_at=now() WHERE id='resource-1'"))
        master.run('resources', full=True)
        assert query('conduction')['total'] == 0
    assert all('authors' not in row.get('fields', {}) for row in service.store.get_many([version_id]).values())
