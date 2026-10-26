"""Full lexical parity on fixture-owned REAL PG/Mongo, synthetic content/fake3D.

Uses the normal login/CSRF/session boundary. Not a production corpus acceptance.
"""
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from pymongo import MongoClient
from sqlalchemy import event
from starlette.requests import Request

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.lexical_schema import lexical_contract
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.api.search_access import search_access
from data_sync_etl.application.lexical_export import TrustedLexicalExporter, verify_export
from data_sync_etl.application.search_evaluation_snapshot import replay_lexical_snapshot
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


def test_real_pg_mongo_session_authorized_full_lexical_parity(searchable, mongo_test_database, tmp_path):
    c, _ = searchable
    metadata_query = []
    def capture_query(conn, cursor, statement, parameters, context, executemany):
        if (not metadata_query and statement.lstrip().upper().startswith('SELECT')
                and 'resource_versions' in statement and 'master_learning_resources' in statement):
            metadata_query.append((statement, parameters))
    event.listen(c.engine, 'before_cursor_execute', capture_query)
    with MongoClient(mongo_test_database[0], tz_aware=True, serverSelectionTimeoutMS=3000) as mongo:
        db = mongo[mongo_test_database[1]]
        assert db.name.startswith('etl_test_')
        initialize(db, apply=True, dimension=3)
        db.command('collMod', 'content_bags', validator=lexical_contract(3))
        db.mongo_content_units.insert_many(list(c.documents.units.values()))
        c.indexing.reader = MongoContentReader(db)
        c.indexing.bags = MongoBagStore(db, 3)
        c.search.bags = c.indexing.bags
        c.indexing.enqueue(searchable[1]['id'], force=True)
        assert c.indexing.once()
        files = {}
        password = secrets.token_urlsafe(24)
        for key, value in [('hash', PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(password)),
                           ('key', secrets.token_hex(32)), ('proxy', secrets.token_hex(32))]:
            files[key] = tmp_path/key
            files[key].write_text(value, encoding='ascii')
        c.settings = c.settings.model_copy(update={'auth_mode': 'single_admin', 'admin_enabled': True,
            'app_environment': 'production', 'auth_deployment_profile': 'localhost', 'auth_origin': 'http://127.0.0.1',
            'admin_login_email': 'operator@etl.test', 'admin_password_hash_file': files['hash'],
            'session_signing_key_file': files['key'], 'auth_proxy_key_file': files['proxy'],
            'auth_state_path': tmp_path/'sessions.db'})
        app = create_app(c)
        with TestClient(app, base_url='http://127.0.0.1', headers={
            'Origin': 'http://127.0.0.1', 'X-ETL-Proxy-Key': files['proxy'].read_text()}) as api:
            api.headers['X-CSRF-Token'] = api.get('/api/v1/auth/csrf').json()['csrf_token']
            assert api.post('/api/v1/auth/login', json={'email': c.settings.admin_login_email, 'password': password}).status_code == 200
            api.headers['X-CSRF-Token'] = api.get('/api/v1/auth/csrf').json()['csrf_token']
            request = Request({'type': 'http', 'method': 'POST', 'path': '/api/v1/search', 'scheme': 'http',
                'server': ('127.0.0.1', 80), 'client': ('127.0.0.1', 50000), 'query_string': b'',
                'app': app, 'headers': [(b'cookie', ('etl_session='+api.cookies['etl_session']).encode())]})
            def principal():
                # Existing single_admin SearchAccess lacks actor_id. Bind the ID
                # from the SAME verified server session, never from request JSON.
                access = search_access(request)
                return replace(access, actor_id=app.state.auth.principal(request).id)
            exporter = TrustedLexicalExporter(c.search, principal, environment='approved_staging')
            payload = exporter.capture()
            snapshot = verify_export(payload)
            comparisons = 0
            for query in ('page text', 'PAGE TEXT', 'SQL', 'C++', 'no matching word', 'page page'):
                scores = replay_lexical_snapshot(query, snapshot)
                expected = {}
                for bag in snapshot.bags:
                    if bag.bag_id in scores:
                        expected[bag.resource_id] = max(expected.get(bag.resource_id, 0), scores[bag.bag_id])
                for page in (1, 2):
                    response = api.post('/api/v1/search', json={'query': query, 'method': 'bow', 'page': page, 'size': 1})
                    assert response.status_code == 200, response.text
                    actual = response.json()
                    order = sorted(expected, key=lambda r: (-expected[r], r))
                    assert actual['total'] == len(order)
                    assert [i['resource_id'] for i in actual['items']] == order[page-1:page]
                    assert all(i['score'] == pytest.approx(expected[i['resource_id']]) for i in actual['items'])
                    comparisons += 1
            root = __import__('os').environ.get('ETL_PHASE3_HTTP_EVIDENCE')
            if root:
                from data_sync_etl.application.search_review import write_review
                write_review(Path(root)/'pg-mongo-lexical-snapshot.json', payload)
                write_review(Path(root)/'pg-mongo-parity.json', {'status': 'PASS_VERIFIED_TEST_CORPUS',
                    'storage': 'REAL_POSTGRESQL_AND_MONGODB', 'corpus': 'SYNTHETIC_FIXTURE',
                    'model': 'FAKE3D_NOT_SEMANTIC', 'authentication': 'NORMAL_SINGLE_ADMIN_SESSION',
                    'comparisons': comparisons, 'bags': len(snapshot.bags), 'checksum': payload['checksum']})
                assert metadata_query
                statement, parameters = metadata_query[0]
                with c.engine.connect() as connection:
                    pg_plan = connection.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+statement,
                                                         parameters).scalar_one()[0]
                selector = {'resource_version_id': {'$in': [b.version_id for b in snapshot.bags]},
                    'is_current': True, 'embedding_model': f'{c.indexing.embedding.model_id}@{c.indexing.embedding.version}'}
                mongo_plan = db.command('explain', {'find': 'content_bags', 'filter': selector,
                    'sort': {'_id': 1}, 'limit': c.settings.search_max_bags+1}, verbosity='executionStats')
                # Private plans may include IDs. Public reports publish only
                # stage/index names and examined counts, never predicates/IDs.
                write_review(Path(root)/'database-plans-private.json', {'scope': 'TINY_SYNTHETIC_TEST_CORPUS',
                    'postgres_actual_search_metadata_select': pg_plan, 'mongo_search_candidate_selector': mongo_plan})
            assert api.post('/api/v1/auth/logout').status_code == 204
            with pytest.raises(Exception) as error:
                exporter.capture()
            assert getattr(error.value, 'code', '').startswith('AUTH_')
