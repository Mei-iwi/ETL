"""Real SQLAlchemy/SQLite and Index builder, typed Mongo/model doubles; no Dev access."""

from copy import deepcopy

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
    with TestClient(create_app(c)) as api:
        for method in ('bow', 'vector', 'hybrid'):
            response = api.post('/api/v1/search', json={'query': 'page text', 'method': method})
            assert response.status_code == 200, response.text
            item = response.json()['items'][0]
            assert item['resource_version_id'] == version['id']
            assert 'embedding' not in item
        assert all(len(bag.embedding) == 384 for bag in c.indexing.bags.rows.values())
    assert before == (c.documents.units, c.indexing.bags.rows)
