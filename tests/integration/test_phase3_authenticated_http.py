"""REAL loopback HTTP/session, SQLite/memory/fake3D fixtures, NOT staging DB/model.

No forged principal/JWT, auth dependency override or permission bypass. Generated
temporary hash/proxy secrets are used by the normal production auth middleware.
"""
import json
import secrets
import socket
from threading import Thread
from time import monotonic, sleep

import httpx
import pytest
import uvicorn
from argon2 import PasswordHasher
from pydantic import ValidationError

from data_sync_etl.main import create_app
from scripts.benchmark_authenticated_search import HttpProfile, benchmark
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('concurrency', [1, 2])
def test_real_authenticated_http_search_session_and_redacted_profile(searchable, tmp_path, concurrency):
    c, _ = searchable
    password = secrets.token_urlsafe(24)
    files = {}
    for name, value in [('hash', PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(password)),
                        ('key', secrets.token_hex(32)), ('proxy', secrets.token_hex(32))]:
        files[name] = tmp_path/name
        files[name].write_text(value, encoding='ascii')
    c.settings = c.settings.model_copy(update={'auth_mode': 'single_admin', 'admin_enabled': True,
        'app_environment': 'production', 'auth_deployment_profile': 'localhost',
        'admin_login_email': 'admin@etl.test', 'admin_password_hash_file': files['hash'],
        'session_signing_key_file': files['key'], 'auth_proxy_key_file': files['proxy'],
        'auth_state_path': tmp_path/'sessions.db'})
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    c.settings.auth_origin = origin
    password_path = tmp_path/'password'
    password_path.write_text(password, encoding='utf-8')
    server = uvicorn.Server(uvicorn.Config(create_app(c), host='127.0.0.1', port=port,
        proxy_headers=False, access_log=False, log_level='error'))
    thread = Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        end = monotonic()+10
        while not server.started and thread.is_alive() and monotonic() < end:
            sleep(.02)
        assert server.started
        assert httpx.post(origin+'/api/v1/search', json={'query': 'page text', 'method': 'bow'}).status_code == 403
        profile = HttpProfile(environment='isolated_http_fixture', base_url=origin, email=c.settings.admin_login_email,
            password_file=str(password_path), proxy_key_file=str(files['proxy']), expected_vector_path='legacy_bag',
            queries=['page text', 'PAGE TEXT', 'lá xanh', 'green leaf', 'feuille verte', 'page text français',
                     'C++', 'SQL', 'nonexistent identifier', 'page text page'], repeats=2, concurrency=concurrency)
        observed = []
        result = benchmark(profile, observer=lambda method, payload: observed.append((method, payload)))
        assert result['errors'] == result['timeouts'] == 0 and result['requests'] == 60
        assert result['endpoint_checks']['unauthenticated_search'] == 401
        assert result['endpoint_checks']['revoked_session'] == 401
        assert all(method['samples'] == 20 and method['p95_ms'] for method in result['latency'].values())
        assert {method for method, _ in observed} == {'bow', 'vector', 'hybrid'}
        assert any(payload['items'] for _, payload in observed)
        assert all(payload['access_scope'] == 'authenticated_admin' for _, payload in observed)
        assert password not in json.dumps(result) and files['proxy'].read_text() not in json.dumps(result)
        # Public report only aggregates; keep raw per-request timing private.
        output = __import__('os').environ.get('ETL_PHASE3_HTTP_EVIDENCE')
        if output:
            from data_sync_etl.application.search_review import write_review
            write_review(f'{output}/http-fixture-c{concurrency}.json', result)
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()
        assert not thread.is_alive()


def test_http_profile_blocks_unbounded_workload_credentials_in_url_or_plain_lan():
    base = dict(environment='approved_staging', base_url='http://127.0.0.1:18080', email='reviewer@example.invalid',
        password_file='private/password', proxy_key_file='private/proxy', expected_vector_path='meaning_bag', queries=['query'])
    for updates in ({'base_url': 'http://user:pass@127.0.0.1'}, {'base_url': 'http://192.0.2.1'},
                    {'concurrency': 32}, {'environment': 'production'}, {'base_url': 'https://host.test/?token=secret'}):
        with pytest.raises(ValidationError):
            HttpProfile(**{**base, **updates})
