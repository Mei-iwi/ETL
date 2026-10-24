"""Real BGE-M3+PG+Mongo+HTTP auth on isolated SYNTHETIC test data only."""
import json
import os
import secrets
import socket
from pathlib import Path
from threading import Thread
from time import monotonic, process_time, sleep

import pytest
import uvicorn
from argon2 import PasswordHasher
from pymongo import MongoClient
from pymongo.monitoring import CommandListener
from sqlalchemy import event

from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.lexical_schema import lexical_contract
from data_sync_etl.adapters.mongo.meaning_schema import meaning_contract
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.adapters.mongo.semantic import MongoSemanticStore, install
from data_sync_etl.application.search_review import write_review
from data_sync_etl.application.semantic import SemanticService
from data_sync_etl.domain.evaluation import percentile
from data_sync_etl.main import create_app
from scripts.benchmark_authenticated_search import HttpProfile, benchmark
from tests.unit.test_search import searchable as searchable

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(os.environ.get('ETL_PHASE3_REAL_SEMANTIC') != '1',
    reason='Explicit isolated PG/Mongo and real BGE-M3 1024D required')]


@pytest.fixture
def container(pg_container):
    return pg_container


@pytest.mark.parametrize('vector_path', ['semantic_chunk', 'meaning_bag'])
@pytest.mark.parametrize('concurrency', [1, 2])
def test_real_semantic_index_authenticated_http_and_context(searchable, mongo_test_database, tmp_path, vector_path, concurrency):
    import psutil
    import torch
    torch.set_num_threads(2)
    c, version = searchable
    use_semantic = vector_path == 'semantic_chunk'
    model = (BgeM3Embedding(os.environ['BGE_M3_TEST_PATH'], os.environ['BGE_M3_TEST_REVISION'], max_tokens=512)
             if use_semantic else LocalSentenceEmbedding(os.environ['BGE_M3_TEST_PATH'], 'BAAI/bge-m3',
                os.environ['BGE_M3_TEST_REVISION'], 1024))
    started = monotonic()
    model.prepare()
    load_seconds = monotonic()-started
    loaded_model = model._model
    mongo_times, pg_times, encoding_times = [], [], []
    measuring = [False]
    class MongoTimings(CommandListener):
        def started(self, event):
            pass
        def succeeded(self, event):
            if measuring[0]:
                mongo_times.append({'command': event.command_name, 'ms': event.duration_micros/1000})
        def failed(self, event):
            pass
    def pg_before(conn, cursor, statement, parameters, context, executemany):
        context.phase3_started = monotonic()
    def pg_after(conn, cursor, statement, parameters, context, executemany):
        if measuring[0]:
            pg_times.append((monotonic()-context.phase3_started)*1000)
    event.listen(c.engine, 'before_cursor_execute', pg_before)
    event.listen(c.engine, 'after_cursor_execute', pg_after)
    class DenseBagAdapter:
        # Same real resident model supplies the legacy Bag prerequisite; no
        # additional SentenceTransformer instance or fake dense vectors.
        model_id, dimension, version = model.model_id, 1024, os.environ['BGE_M3_TEST_REVISION']
        def available(self):
            return model.is_available()
        def prepare(self):
            model.prepare()
        def embed(self, text):
            return model.embed_queries([text])[0]['vector']
    with MongoClient(mongo_test_database[0], tz_aware=True, serverSelectionTimeoutMS=3000,
                     event_listeners=[MongoTimings()]) as mongo:
        db = mongo[mongo_test_database[1]]
        initialize(db, apply=True, dimension=1024)
        db.command('collMod', 'content_bags', validator=lexical_contract(1024) if use_semantic else meaning_contract(1024))
        if use_semantic:
            install(db)
        db.mongo_content_units.insert_many(list(c.documents.units.values()))
        c.indexing.reader, c.indexing.bags = MongoContentReader(db), MongoBagStore(db, 1024)
        c.indexing.embedding = DenseBagAdapter() if use_semantic else model
        c.settings.index_embedding_dimension = 1024
        c.settings.index_meaning_bags_enabled = not use_semantic
        c.search.bags = c.indexing.bags
        c.indexing.enqueue(version['id'], force=True)
        assert c.indexing.once()
        service = SemanticService(c.indexing, model, MongoSemanticStore(db), c.settings) if use_semantic else None
        if service:
            assert service.build(version['id'], apply=True)['status'] == 'READY'
        readiness = service.status(version['id']) if service else c.indexing.status(version['id'])
        assert readiness['coverage_verified'] if service else readiness['index_status'] == 'READY'
        c.search.semantic = service
        c.settings.semantic_enabled = use_semantic
        c.semantic = service
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        origin = f'http://127.0.0.1:{sock.getsockname()[1]}'
        password = secrets.token_urlsafe(24)
        files = {}
        for name, value in [('hash', PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(password)),
                            ('key', secrets.token_hex(32)), ('proxy', secrets.token_hex(32)), ('password', password)]:
            files[name] = tmp_path/name
            files[name].write_text(value, encoding='ascii')
        c.settings = c.settings.model_copy(update={'auth_mode': 'single_admin', 'admin_enabled': True,
            'app_environment': 'production', 'auth_deployment_profile': 'localhost', 'auth_origin': origin,
            'admin_login_email': 'operator@etl.test', 'admin_password_hash_file': files['hash'],
            'session_signing_key_file': files['key'], 'auth_proxy_key_file': files['proxy'],
            'auth_state_path': tmp_path/'sessions.db'})
        server = uvicorn.Server(uvicorn.Config(create_app(c), host='127.0.0.1', port=sock.getsockname()[1],
                                               proxy_headers=False, access_log=False, log_level='error'))
        thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
        thread.start()
        try:
            end = monotonic()+120
            while thread.is_alive() and monotonic() < end and (not server.started or not model.search_status()['ready']):
                sleep(.05)
            assert server.started and model.search_status()['ready']
            profile = HttpProfile(environment='approved_staging', base_url=origin, email=c.settings.admin_login_email,
                password_file=str(files['password']), proxy_key_file=str(files['proxy']), expected_vector_path=vector_path,
                queries=['page text', 'Page one text', 'page two', 'page three', 'text', 'native text extraction',
                         'green leaf', 'feuille verte', 'page text français', 'quantum banana'], repeats=2, concurrency=concurrency)
            observed = []
            encode = model.embed_queries if use_semantic else model._encode
            def measured_encode(queries):
                start = monotonic()
                result = encode(queries)
                encoding_times.append((monotonic()-start)*1000)
                return result
            if use_semantic:
                model.embed_queries = measured_encode
            else:
                model._encode = measured_encode
            measuring[0] = True
            cpu_start, wall_start = process_time(), monotonic()
            result = benchmark(profile, observer=lambda method, payload: observed.append((method, payload)))
            process_cpu_seconds, total_wall_seconds = process_time()-cpu_start, monotonic()-wall_start
            measuring[0] = False
            assert result['errors'] == result['timeouts'] == 0
            assert len(encoding_times) == 42  # 2 first + 40 warm Vector/Hybrid; zero BoW query encodes.
            assert model._model is loaded_model
            for method, payload in observed:
                if method != 'bow' and use_semantic:
                    assert payload['vector_index']['dimension'] == 1024
                    assert payload['vector_index']['strategy'] == 'contextual'
            result.update({'scope': 'ISOLATED_SYNTHETIC_STAGING_REAL_PG_MONGO_BGE_HTTP_NOT_HUMAN_QUALITY',
                'model': model.model_identity() if use_semantic else {'model_id': model.model_id,
                    'revision': model.version, 'dimension': model.dimension, 'engine': 'SentenceTransformer'},
                'model_load_seconds': load_seconds, 'model_instances': 1,
                'peak_process_rss_bytes': psutil.Process().memory_info().peak_wset if os.name == 'nt'
                    else __import__('resource').getrusage(__import__('resource').RUSAGE_SELF).ru_maxrss*1024,
                'process_cpu_seconds': process_cpu_seconds, 'measured_wall_seconds': total_wall_seconds,
                'query_encode_calls': len(encoding_times),
                'server_stages': {name: {'calls': len(values), 'sum_ms': sum(values),
                    'p50_ms': percentile(values, .5), 'p95_ms': percentile(values, .95)} for name, values in
                    [('postgres_statements', pg_times), ('mongo_commands', [r['ms'] for r in mongo_times]),
                     ('query_encoding', encoding_times)]},
                'readiness': {k: v for k, v in readiness.items() if k in ('status', 'coverage_verified', 'chunk_count')}})
            root = os.environ.get('ETL_PHASE3_HTTP_EVIDENCE')
            if root:
                write_review(Path(root)/(vector_path+'-http.json'), result)
            assert 'password' not in json.dumps(result)
        finally:
            server.should_exit = True
            thread.join(30)
            sock.close()
            assert not thread.is_alive()
