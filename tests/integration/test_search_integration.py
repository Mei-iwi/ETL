"""Real SQLAlchemy/SQLite and Index builder, typed Mongo/model doubles; no Dev access."""

import os
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


def test_search_384_dimension_end_to_end(searchable):
    c, version = searchable
    c.settings.index_embedding_dimension = 384
    c.indexing.embedding.dimension = 384
    c.indexing.bags.dimension = 384
    c.indexing.embedding.embed = lambda text: [0.5] * 384
    c.indexing.enqueue(version['id'], force=True)
    assert c.indexing.once()
    assert c.indexing.status(version['id'])['index_status'] == 'READY'
    before = deepcopy(c.documents.units), deepcopy(c.indexing.bags.rows)
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        for method in ('bow', 'vector', 'hybrid'):
            response = api.post('/api/v1/search', json={'query': 'page text', 'method': method})
            assert response.status_code == 200, response.text
            item = response.json()['items'][0]
            assert item['resource_version_id'] == version['id']
            assert 'embedding' not in item
            context = api.get(item['context_url'])
            assert context.status_code == 200, context.text
            selected = next(chunk for chunk in context.json()['chunks'] if chunk['selected'])
            assert selected['text'].startswith(item['snippet'])
        assert all(len(bag.embedding) == 384 for bag in c.indexing.bags.rows.values())
    assert before == (c.documents.units, c.indexing.bags.rows)


@pytest.mark.skipif(os.environ.get('ETL_SEARCH_REAL_MODEL') != '1', reason='Real local model test is opt-in')
def test_real_local_model_search_and_context(searchable):
    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
    from data_sync_etl.config import Settings

    c, version = searchable
    # Read only model configuration. Never use these DB URLs for this test container.
    local = Settings()
    c.indexing.embedding = LocalSentenceEmbedding(local.index_embedding_model_path,
        local.index_embedding_model_id, local.index_embedding_model_version, local.index_embedding_dimension)
    assert c.indexing.embedding.available(), 'Provision local model before opting in'
    c.settings.index_embedding_dimension = 384
    c.indexing.bags.dimension = 384
    c.indexing.enqueue(version['id'], force=True)
    assert c.indexing.once()
    assert c.indexing.status(version['id'])['index_status'] == 'READY'
    assert len(c.indexing.embedding.embed('Học liệu tiếng Việt về phép tính và phân số.')) == 384
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        for method in ('bow', 'vector', 'hybrid'):
            response = api.post('/api/v1/search', json={'query': 'page text', 'method': method})
            assert response.status_code == 200, response.text
            item = response.json()['items'][0]
            assert api.get(item['context_url']).status_code == 200
    assert all(len(bag.embedding) == 384 for bag in c.indexing.bags.rows.values())
