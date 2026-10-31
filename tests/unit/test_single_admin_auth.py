import json
import re
import secrets
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient

from data_sync_etl.main import create_app
from data_sync_etl.security.auth import AuthService, validate_mode
from tests.unit.test_search import searchable as searchable
from tests.unit.test_source_reference import source_viewer as source_viewer


@pytest.fixture(scope='module')
def credentials():
    password = secrets.token_urlsafe(24)
    return password, PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(password)


@pytest.fixture
def configured(container, tmp_path, credentials):
    password, encoded = credentials
    files = {}
    for name, value in [('hash', encoded), ('key', secrets.token_hex(32)), ('proxy', secrets.token_hex(32))]:
        files[name] = tmp_path / name
        files[name].write_text(value, encoding='ascii')
    container.settings = container.settings.model_copy(update={
        'auth_mode': 'single_admin', 'admin_enabled': True,
        'app_environment': 'production', 'auth_deployment_profile': 'localhost',
        'auth_origin': 'http://127.0.0.1', 'admin_login_email': 'admin@etl.test',
        'admin_password_hash_file': files['hash'], 'session_signing_key_file': files['key'],
        'auth_proxy_key_file': files['proxy'], 'auth_state_path': tmp_path / 'auth' / 'sessions.db',
    })
    return container, password, files


def make_client(configured):
    container, _, files = configured
    return TestClient(create_app(container), base_url='http://127.0.0.1',
                      headers={'X-ETL-Proxy-Key': files['proxy'].read_text(), 'Origin': 'http://127.0.0.1'})


def csrf(client):
    response = client.get('/api/v1/auth/csrf')
    assert response.status_code == 200
    return {'X-CSRF-Token': response.json()['csrf_token']}


def login(client, password, **extra):
    return client.post('/api/v1/auth/login', headers=csrf(client),
                       json={'email': 'admin@etl.test', 'password': password, **extra})


def test_login_rotation_logout_and_no_plaintext_store(configured):
    _, password, _ = configured
    with make_client(configured) as api:
        assert api.get('/api/v1/auth/config').json()['login_required'] is True
        assert api.get('/api/v1/auth/me').status_code == 401
        response = login(api, password)
        assert response.status_code == 200
        cookie = response.headers['set-cookie']
        assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Domain=' not in cookie
        first = api.cookies.get('etl_session')
        assert api.get('/api/v1/auth/me').json()['role'] == 'ADMIN'
        assert login(api, password).status_code == 200
        second = api.cookies.get('etl_session')
        assert first != second
        auth = api.app.state.auth
        assert auth.store.get(auth.digest(first.encode()), auth.clock()) is None
        assert password.encode() not in Path(auth.settings.auth_state_path).read_bytes()
        assert second.encode() not in Path(auth.settings.auth_state_path).read_bytes()
        assert api.post('/api/v1/auth/logout', headers=csrf(api)).status_code == 204
        assert api.get('/api/v1/auth/me').status_code == 401


def test_generic_failures_and_persistent_bounded_throttle(configured):
    _, password, _ = configured
    with make_client(configured) as api:
        headers = csrf(api)
        a = api.post('/api/v1/auth/login', headers=headers, json={'email': 'wrong@etl.test', 'password': password})
        b = api.post('/api/v1/auth/login', headers=headers, json={'email': 'admin@etl.test', 'password': 'wrong'})
        assert a.status_code == b.status_code == 401 and a.json() == b.json()
        for _ in range(4):
            api.post('/api/v1/auth/login', headers=headers, json={'email': 'wrong@etl.test', 'password': 'wrong'})
        limited = api.post('/api/v1/auth/login', headers=headers, json={'email': 'admin@etl.test', 'password': password})
        assert limited.status_code == 429 and int(limited.headers['retry-after']) <= 61
    with make_client(configured) as api:
        assert login(api, password).status_code == 429


@pytest.mark.parametrize('bad', ['csrf', 'origin', 'host', 'forwarded', 'proxy', 'forged-cookie'])
def test_invalid_provenance_csrf_and_forged_cookie(configured, bad):
    _, password, _ = configured
    with make_client(configured) as api:
        headers = csrf(api)
        if bad == 'csrf':
            headers['X-CSRF-Token'] = 'invalid'
        if bad == 'origin':
            headers['Origin'] = 'http://attacker.test'
        if bad == 'host':
            headers['Host'] = 'attacker.test'
        if bad == 'forwarded':
            headers['X-Forwarded-For'] = '127.0.0.1'
        if bad == 'proxy':
            headers['X-ETL-Proxy-Key'] = 'forged'
        if bad == 'forged-cookie':
            api.cookies.set('etl_session', secrets.token_urlsafe(32))
            assert api.get('/api/v1/resources').status_code == 401
        else:
            response = api.post('/api/v1/auth/login', headers=headers,
                                json={'email': 'admin@etl.test', 'password': password})
            assert response.status_code == 403
            assert password not in response.text


def test_idle_absolute_remember_and_restart_persistence(configured):
    _, password, _ = configured
    with make_client(configured) as api:
        auth = api.app.state.auth
        now = [10000.0]
        auth.clock = lambda: now[0]
        assert login(api, password).status_code == 200
        token = api.cookies.get('etl_session')
        replacement = AuthService(auth.settings, clock=lambda: now[0])
        assert replacement.store.get(replacement.digest(token.encode()), now[0])
        now[0] += auth.settings.auth_idle_seconds + 1
        assert api.get('/api/v1/auth/me').status_code == 401
        assert login(api, password, remember_me=True).status_code == 200
        assert 'Max-Age=' in api.get('/api/v1/auth/csrf').headers['set-cookie']
        now[0] += auth.settings.auth_idle_seconds + 1
        assert api.get('/api/v1/auth/me').status_code == 200
        now[0] += auth.settings.auth_remember_seconds + 1
        assert api.get('/api/v1/auth/me').status_code == 401


def test_logout_parallel_atomic_and_key_rotation(configured):
    _, password, files = configured
    with make_client(configured) as api:
        assert login(api, password).status_code == 200
        auth = api.app.state.auth
        digest = auth.digest(api.cookies.get('etl_session').encode())
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(auth.store.revoke, [digest] * 12))
        assert auth.store.get(digest, auth.clock()) is None
        assert login(api, password).status_code == 200
        files['key'].write_text(secrets.token_hex(32))
        changed = AuthService(auth.settings)
        assert changed.store.get(changed.digest(api.cookies.get('etl_session').encode()), changed.clock()) is None


def test_admin_flag_is_not_login_and_disables_management(configured):
    container, password, _ = configured
    with make_client(configured) as api:
        assert api.get('/api/v1/resources').status_code == 401
        assert login(api, password).status_code == 200
        container.settings.admin_enabled = False
        response = api.post('/api/v1/master-data/sync', headers=csrf(api), json={})
        assert response.status_code == 403
        assert api.get('/api/v1/auth/me').status_code == 200


def test_anonymous_exhaustive_routes_deny_before_handler(configured):
    with make_client(configured) as api:
        checked = 0
        # Include separately registered metadata/reconciliation routers too.
        from data_sync_etl.main import (
            account_router,
            profile_router,
            reconciliation_router,
            resource_description_router,
            router,
        )
        routes = [route for group in (router, account_router, profile_router,
                  reconciliation_router, resource_description_router) for route in group.routes]
        for route in routes:
            path = getattr(route, 'path', '')
            if not (path.startswith('/api/') or path.startswith('/admin/')) or path.startswith('/api/v1/auth/'):
                continue
            for method in route.methods - {'HEAD', 'OPTIONS'}:
                url = re.sub(r'\{[^}]+\}', 'unknown', path)
                response = api.request(method, url, json={})
                assert response.status_code == 401, (method, path, response.status_code)
                checked += 1
        assert checked >= 47


@pytest.mark.parametrize('patch', [
    {'auth_mode': 'single_admin', 'auth_dev_admin_bypass': True},
    {'auth_mode': 'local_demo', 'auth_dev_admin_bypass': True, 'app_environment': 'production'},
    {'auth_mode': 'local_demo', 'auth_dev_admin_bypass': True, 'app_environment': 'local', 'auth_origin': 'http://192.0.2.1'},
    {'auth_mode': 'local_demo', 'auth_dev_admin_bypass': True, 'app_environment': 'local', 'auth_public_enabled': True},
    {'auth_mode': 'single_admin', 'auth_deployment_profile': 'multi_machine', 'auth_public_enabled': True},
])
def test_invalid_modes_fail_startup(configured, patch):
    settings = configured[0].settings.model_copy(update=patch)
    with pytest.raises(ValueError):
        validate_mode(settings)


def test_local_bypass_requires_gateway_and_keeps_csrf(configured):
    container, _, _ = configured
    container.settings.auth_mode = 'local_demo'
    container.settings.auth_dev_admin_bypass = True
    container.settings.app_environment = 'local'
    with make_client(configured) as api:
        assert api.get('/api/v1/auth/config').json()['login_required'] is False
        assert api.get('/api/v1/auth/me').json()['mode'] == 'local_demo'
        assert api.get('/api/v1/auth/me', headers={'X-ETL-Proxy-Key': 'forged'}).status_code == 403
        assert api.post('/api/v1/master-data/sync', json={}).status_code == 403


def test_tls_cookie_and_secret_validation(configured):
    container, password, files = configured
    container.settings.auth_origin = 'https://etl.example.test'
    container.settings.auth_deployment_profile = 'multi_machine'
    container.settings.auth_public_enabled = True
    container.settings.auth_cookie_secure = True
    with TestClient(create_app(container), base_url='https://etl.example.test', headers={
        'X-ETL-Proxy-Key': files['proxy'].read_text(), 'Origin': 'https://etl.example.test',
    }) as api:
        response = login(api, password)
        assert response.status_code == 200
        assert '__Host-etl_session=' in response.headers['set-cookie']
        assert 'Secure' in response.headers['set-cookie'] and 'HttpOnly' in response.headers['set-cookie']
    files['hash'].write_text('invalid')
    with pytest.raises(ValueError, match='AUTH_PASSWORD_HASH_INVALID'):
        AuthService(container.settings)


def test_auth_public_health_does_not_query_database(configured):
    container = configured[0]
    container.uow = lambda: pytest.fail('Health must not query DB in auth mode')
    with make_client(configured) as api:
        assert api.get('/health').json() == {'status': 'ok'}


@pytest.mark.parametrize('available', [True, False])
def test_authenticated_system_health_checks_database_without_leaking_errors(configured, available):
    container, password, _ = configured
    calls = []

    class Repository:
        def health(self):
            calls.append('read')
            if not available:
                raise RuntimeError('private connection information')

    @contextmanager
    def uow():
        yield Repository()

    container.uow = uow
    with make_client(configured) as api:
        assert api.get('/api/v1/system/health').status_code == 401
        assert calls == []
        assert login(api, password).status_code == 200
        response = api.get('/api/v1/system/health')
        assert response.status_code == (200 if available else 503)
        assert response.json() == {'status': 'ok', 'database': 'ok' if available else 'unavailable'}
        assert response.headers['Cache-Control'] == 'private, no-store'
        assert 'private connection information' not in response.text
        assert calls == ['read']


def test_oversized_login_rejected_without_password_echo(configured):
    with make_client(configured) as api:
        payload = json.dumps({'email': 'admin@etl.test', 'password': secrets.token_urlsafe(10000)})
        response = api.post('/api/v1/auth/login', headers=csrf(api), content=payload)
        assert response.status_code == 413 and payload not in response.text


def test_authenticated_source_download_and_private_restrictions(source_viewer, configured, monkeypatch):
    container, version, source, original = source_viewer
    assert container is configured[0]
    url = f"/api/v1/resource-versions/{version['id']}/file"
    monkeypatch.setattr(container.storage, 'get_local_path', lambda *_: pytest.fail('No SOURCE legacy storage read'))
    with make_client(configured) as api:
        assert api.get(url).status_code == 401
        assert login(api, configured[1]).status_code == 200
        response = api.get('/api/v1/resources?page=1&size=10&q=')
        assert response.status_code == 200, response.text
        rejected = api.patch(f"/api/v1/resources/{version['resource_id']}/metadata", headers=csrf(api), json={'title': 'Must not change SOURCE'})
        assert rejected.status_code == 409 and rejected.json()['detail']['code'] == 'RESOURCE_SOURCE_OWNED_READ_ONLY'
        response = api.get(url)
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        range_response = api.get(url, headers={'Range': 'bytes=0-3'})
        assert range_response.status_code == 409
        download = api.get(url + '?download=true')
        assert download.status_code == 409
        assert download.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        source['source_hash'] = '0' * 64
        assert api.get(url).status_code == 409
        source['source_hash'] = version['source_hash']
        with container.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'publication_status': 'ARCHIVED'})
        assert api.get(url).status_code == 404
        assert api.get(url + '?view=text').status_code == 404


def test_source_rechecks_session_after_temporary_file_preparation(source_viewer, configured, monkeypatch):
    from contextlib import contextmanager
    container, version, _, _ = source_viewer
    original = container.source_resolver.temporary_path
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        auth = api.app.state.auth
        token = api.cookies.get(auth.cookie_name)

        @contextmanager
        def revoke_before_stream(*args, **kwargs):
            with original(*args, **kwargs) as path:
                auth.store.revoke(auth.digest(token.encode()))
                yield path

        monkeypatch.setattr(container.source_resolver, 'temporary_path', revoke_before_stream)
        response = api.get(f"/api/v1/resource-versions/{version['id']}/file")
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
    assert not list(container.settings.source_temp_root.glob('attempt-*'))


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_authenticated_search_uses_server_scope_and_preserves_eligibility(searchable, configured, method):
    container, version = searchable
    assert container is configured[0]
    container.settings.search_internal_dev_enabled = False
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['access_scope'] == 'authenticated_admin' and body['total'] == 1
        context_id = body['items'][0]['context_id']
        assert api.get('/api/v1/chunks/' + context_id + '/context').status_code == 200
        with container.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
        response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
        assert response.status_code == 200 and response.json()['total'] == 0


def test_password_rotation_preflight_cannot_leave_old_process_authorized(configured):
    container, password, files = configured
    with make_client(configured) as api:
        assert login(api, password).status_code == 200
        old = api.app.state.auth
        # Simulate the deployment preflight sharing the auth volume while the old
        # API still runs. Even the same signing key must not accept old credentials.
        files['hash'].write_text(PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(secrets.token_urlsafe(24)))
        replacement = AuthService(container.settings)
        assert replacement.store.active() and not old.store.active()
        assert not old.store.create(old.digest(secrets.token_bytes(32)), old.clock(), 300, 60)
        assert api.get('/api/v1/auth/me').status_code == 401
        assert login(api, password).status_code == 503


def test_non_ascii_csrf_header_is_rejected_without_server_error(configured):
    with make_client(configured) as api:
        csrf(api)
        response = api.post('/api/v1/auth/login', headers={'X-CSRF-Token': b'\xff'},
                            json={'email': 'admin@etl.test', 'password': configured[1]})
        assert response.status_code == 403 and response.json()['detail']['code'] == 'AUTH_CSRF_INVALID'
