"""Search HTTP over a READY isolated PG/Mongo corpus; never uses Dev URLs."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from pymongo import MongoClient

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.config import Settings
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(
    os.environ.get('ETL_SEARCH_REAL_MODEL') != '1', reason='Explicit provisioned local384D model required')]


@pytest.fixture
def container(pg_container):
    return pg_container


def test_search_three_methods_real_pg_mongo_model_and_revocation(searchable, mongo_test_database, monkeypatch):
    c, version = searchable
    config = Settings(_env_file=None)
    model = LocalSentenceEmbedding(config.index_embedding_model_path, config.index_embedding_model_id,
                                  config.index_embedding_model_version, config.index_embedding_dimension)
    with MongoClient(mongo_test_database[0], tz_aware=True, serverSelectionTimeoutMS=3000, timeoutMS=10000) as client:
        database = client[mongo_test_database[1]]
        assert database.name.startswith('etl_test_')
        assert initialize(database, apply=True, dimension=384)['ready']
        # Replace only temporary fixture stores with real adapters; build its READY snapshot.
        database.mongo_content_units.insert_many(list(c.documents.units.values()))
        c.indexing.reader = MongoContentReader(database)
        c.indexing.bags = MongoBagStore(database, 384)
        c.indexing.embedding = model
        c.settings.index_embedding_dimension = 384
        c.search.bags = c.indexing.bags
        c.indexing.enqueue(version['id'], force=True)
        assert c.indexing.once()
        assert c.indexing.status(version['id'])['index_status'] == 'READY'
        loaded = model._model
        counts = {'prepare': 0, 'encode': 0}
        prepare, encode = model.prepare, model._encode

        def prepared():
            counts['prepare'] += 1
            return prepare()

        def encoded(text):
            counts['encode'] += 1
            return encode(text)

        monkeypatch.setattr(model, 'prepare', prepared)
        monkeypatch.setattr(model, '_encode', encoded)
        before = {name: list(database[name].find({}).sort('_id', 1)) for name in database.list_collection_names()}
        with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
            for method in ('bow', 'vector', 'hybrid'):
                result = api.post('/api/v1/search', json={'query': 'page text', 'method': method})
                assert result.status_code == 200, result.text
                assert result.json()['items']
                item = result.json()['items'][0]
                context = api.get(item['context_url'])
                assert context.status_code == 200
                selected = next(chunk for chunk in context.json()['chunks'] if chunk['selected'])
                assert selected['text'].startswith(item['snippet'])
                assert 'embedding' not in item
            assert counts == {'prepare': 2, 'encode': 2}  # No model work for BoW; once for each vector request.
            assert model._model is loaded and model.dimension == 384
            entered, release = Event(), Event()

            def held_prepare():
                entered.set()
                assert release.wait(10)
                return prepared()

            monkeypatch.setattr(model, 'prepare', held_prepare)
            with ThreadPoolExecutor(max_workers=1) as first_pool:
                first_request = first_pool.submit(api.post, '/api/v1/search', json={'query': 'page text', 'method': 'vector'})
                try:
                    assert entered.wait(10)
                    with ThreadPoolExecutor(max_workers=8) as burst:
                        competing = list(burst.map(lambda _: api.post('/api/v1/search', json={
                            'query': 'page text', 'method': 'hybrid'}), range(8)))
                    assert all(response.status_code == 503 and response.json()['detail']['code'] == 'SEARCH_RUNTIME_BUSY'
                               for response in competing)
                    assert api.post('/api/v1/search', json={'query': 'page text', 'method': 'bow'}).status_code == 200
                finally:
                    release.set()
                assert first_request.result(timeout=15).status_code == 200
            monkeypatch.setattr(model, 'prepare', prepared)
            assert counts == {'prepare': 3, 'encode': 3} and model._model is loaded
            with c.uow() as repo:
                repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
            for method in ('bow', 'vector', 'hybrid'):
                assert api.post('/api/v1/search', json={'query': 'page text', 'method': method}).json()['items'] == []
            assert api.get(item['context_url']).status_code == 404
        assert before == {name: list(database[name].find({}).sort('_id', 1)) for name in before}
