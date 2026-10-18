"""Two OS ASGI processes, one independently committed writer;384D encoder doubles."""
import multiprocessing
import sys
from unittest.mock import patch

from tests.search_query_cache_support import configure
from tests.unit.test_search import searchable as searchable


def backend(connection, settings_data, database, units, bags):
    from fastapi.testclient import TestClient

    from data_sync_etl.application.indexing import IndexingService
    from data_sync_etl.application.search import SearchService
    from data_sync_etl.composition import Container
    from data_sync_etl.config import Settings
    from data_sync_etl.db.session import make_engine
    from data_sync_etl.main import create_app
    from tests.conftest import MemoryDocumentStore
    from tests.indexing_support import MemoryBags, MemoryReader
    from tests.search_query_cache_support import Runtime384

    Settings.model_config['env_file'] = None
    engine = make_engine(database)
    try:
        documents = MemoryDocumentStore()
        documents.units = units
        settings = Settings(_env_file=None, **settings_data)
        container = Container(settings, engine, documents)
        reader, model = MemoryReader(documents), Runtime384()
        store = MemoryBags(reader)
        store.dimension, store.rows = 384, bags
        store.search_candidates = lambda ids, model, limit: [
            bag for bag in sorted(store.rows.values(), key=lambda bag: bag.id)
            if bag.resource_version_id in ids and bag.embedding_model == model and bag.is_current][:limit]
        container.indexing = IndexingService(container.uow, reader, model, settings, bags=store)
        container.search = SearchService(container.indexing, settings, store, query_cache_runtime=model)
        with patch('data_sync_etl.api.search_access.verified_local_launch', return_value=True):
            with TestClient(create_app(container), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
                first = api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'})
                assert first.status_code == 200 and first.json()['items']
                context = first.json()['items'][0]['context_url']
                connection.send({'warm': True, 'encodes': model.encodes})
                assert connection.poll(30) and connection.recv() == 'writer_committed'
                second = api.post('/api/v1/search', json={'query': 'page', 'method': 'hybrid'})
                connection.send({'status': second.status_code, 'items': second.json()['items'],
                                 'context_status': api.get(context).status_code,
                                 'encodes': model.encodes, 'loads': model.loads,
                                 'cache': container.search._http_query_vectors.stats()})
    except BaseException as exc:
        connection.send({'error_type': type(exc).__name__})
    finally:
        connection.close()
        engine.dispose()


def test_two_cached_backends_observe_independent_writer_revocation(searchable, monkeypatch):
    c, version = searchable
    configure(c, version)
    monkeypatch.delattr(sys.modules['__main__'], '__file__', raising=False)
    context = multiprocessing.get_context('spawn')
    processes, connections = [], []
    try:
        for _ in range(2):
            parent, child = context.Pipe()
            process = context.Process(target=backend, args=(child, c.settings.model_dump(),
                c.engine.url.render_as_string(hide_password=False), c.documents.units, c.indexing.bags.rows))
            process.start()
            child.close()
            processes.append(process)
            connections.append(parent)
        for connection in connections:
            assert connection.poll(45)
            assert connection.recv() == {'warm': True, 'encodes': 1}
        # Third process writes the shared temporary database; no local callback/invalidation.
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
        for connection in connections:
            connection.send('writer_committed')
        for connection in connections:
            assert connection.poll(45)
            value = connection.recv()
            assert value.get('status') == 200 and value['items'] == [], value
            assert value['context_status'] == 404 and value['encodes'] == value['loads'] == 1
            assert value['cache']['hit'] == 1
    finally:
        for connection in connections:
            connection.close()
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()  # Only this test's explicitly created child.
                process.join(timeout=5)
        assert all(process.exitcode == 0 for process in processes)
