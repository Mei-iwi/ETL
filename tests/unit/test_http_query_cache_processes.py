"""Two OS ASGI Backend processes on one temporary read-only SQLite fixture.

Uses a clearly labelled deterministic384D encoder double, not production auth or
an assertion that two full MiniLM instances fit every deployment.
"""
import hashlib
import json
import multiprocessing
import os
import sys
from unittest.mock import patch

from tests.search_query_cache_support import configure
from tests.unit.test_search import searchable as searchable


def backend(connection, settings_data, database, units, bags):
    from data_sync_etl.config import Settings

    Settings.model_config['env_file'] = None
    from fastapi.testclient import TestClient

    from data_sync_etl.application.indexing import IndexingService
    from data_sync_etl.application.search import SearchService
    from data_sync_etl.composition import Container
    from data_sync_etl.db.session import make_engine
    from data_sync_etl.main import create_app
    from tests.conftest import MemoryDocumentStore
    from tests.indexing_support import MemoryBags, MemoryReader
    from tests.search_query_cache_support import Runtime384

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
        signatures = []
        with patch('data_sync_etl.api.search_access.verified_local_launch', return_value=True):
            with TestClient(create_app(container), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
                for page in (1, 2, 1, 3):
                    response = api.post('/api/v1/search', json={'query': 'page', 'method': 'vector', 'page': page, 'size': 1})
                    assert response.status_code == 200
                    signatures.append(hashlib.sha256(json.dumps(response.json(), sort_keys=True).encode()).hexdigest())
        connection.send({'pid': os.getpid(), 'encodes': model.encodes, 'loads': model.loads,
                         'cache': container.search._http_query_vectors.stats(), 'signatures': signatures})
    except BaseException as exc:
        connection.send({'error_type': type(exc).__name__})
    finally:
        connection.close()
        engine.dispose()


def test_two_backend_processes_have_independent_cache_and_loads(searchable, monkeypatch):
    c, version = searchable
    configure(c, version)
    # Do not re-execute a pytest/runpy/stdin launcher in spawned children.
    # The explicit top-level backend target is importable on its own.
    monkeypatch.delattr(sys.modules['__main__'], '__file__', raising=False)
    context = multiprocessing.get_context('spawn')
    connections, processes = [], []
    try:
        for _ in range(2):
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=backend, args=(child, c.settings.model_dump(),
                c.engine.url.render_as_string(hide_password=False), c.documents.units, c.indexing.bags.rows))
            process.start()
            child.close()
            connections.append(parent)
            processes.append(process)
        results = []
        for connection in connections:
            assert connection.poll(60), 'Isolated Backend child did not finish'
            results.append(connection.recv())
        assert all('error_type' not in result for result in results), results
        assert results[0]['pid'] != results[1]['pid']
        assert results[0]['signatures'] == results[1]['signatures']
        for result in results:
            assert result['encodes'] == result['loads'] == 1
            assert result['cache']['published'] == 1 and result['cache']['hit'] == 3
    finally:
        for connection in connections:
            connection.close()
        for process in processes:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()  # Only this test's explicitly owned child.
                process.join(timeout=5)
        assert all(process.exitcode == 0 for process in processes)
