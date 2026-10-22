import hashlib
import os
import secrets
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from data_sync_etl.main import create_app
from data_sync_etl.security.auth import AuthError, validate_mode
from data_sync_etl.security.auth_database import AuthUserRepository
from data_sync_etl.security.database_service import DatabaseAuthService
from tests.unit.test_evaluation import evaluation as evaluation
from tests.unit.test_search import searchable as searchable
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_source_reference import source_viewer as source_viewer


@pytest.fixture
def database_auth(configured, tmp_path, monkeypatch):
    container, password, files = configured
    url = os.environ.get('ETL_AUTH_TEST_DATABASE_URL')
    if url:
        parsed = make_url(url)
        assert os.environ.get('ETL_AUTH_TEST_ISOLATED_ACK') == 'database-auth-20261022'
        assert parsed.host == '127.0.0.1' and parsed.database == 'etl_auth_test'
        owner = create_engine(url, isolation_level='AUTOCOMMIT', hide_parameters=True)
        database = 'etl_auth_test_' + uuid.uuid4().hex
        with owner.connect() as connection:
            connection.execute(text('CREATE DATABASE "' + database + '"'))
        owner.dispose()
        engine = create_engine(parsed.set(database=database), hide_parameters=True)
    else:
        engine = create_engine('sqlite:///' + (tmp_path / 'auth-only.sqlite3').as_posix())
    repo = AuthUserRepository(engine)
    assert repo.migrate() is True
    assert repo.migrate() is False
    repo.check()
    encoded = files['hash'].read_text()
    admin, _ = repo.create('admin@etl.test', 'Administrator', encoded, ['admin'], bootstrap=True)
    container.settings = container.settings.model_copy(update={
        'auth_mode': 'db_users', 'admin_login_email': 'unused@example.invalid',
        'admin_password_hash_file': tmp_path / 'DO_NOT_READ',
        'search_internal_dev_enabled': False,
    })
    monkeypatch.setattr('data_sync_etl.security.auth.create_auth_service',
        lambda settings: DatabaseAuthService(settings, repository=repo))
    yield container, password, files, repo, admin
    engine.dispose()


def client(data):
    container, _, files, *_ = data
    return TestClient(create_app(container), base_url='http://127.0.0.1',
        headers={'X-ETL-Proxy-Key': files['proxy'].read_text(), 'Origin': 'http://127.0.0.1'})


def csrf(api):
    return {'X-CSRF-Token': api.get('/api/v1/auth/csrf').json()['csrf_token']}


def login(api, password, email='admin@etl.test'):
    return api.post('/api/v1/auth/login', headers=csrf(api), json={'email': email, 'password': password})


def make_user(data, role='viewer', **values):
    repo, admin = data[3:]
    account, _ = repo.create(role + '@etl.test', role.title(), repo.get(user_id=admin['id'])['password_hash'], [role], **values)
    repo.change(account['id'], {'must_change_password': False})
    return account


def test_bootstrap_idempotent_and_business_contract_untouched(database_auth):
    _, _, _, repo, admin = database_auth
    before = repo.get(user_id=admin['id'])
    same, created = repo.create(' ADMIN@ETL.TEST ', 'Ignored', 'not a valid hash', ['admin'], bootstrap=True)
    assert not created and same['id'] == admin['id']
    assert repo.get(user_id=admin['id'])['password_hash'] == before['password_hash']
    assert set(inspect(repo.engine).get_table_names()) == {'auth_users', 'auth_roles', 'auth_user_roles', 'auth_schema_versions'}
    from data_sync_etl.db.models import Base
    assert len(Base.metadata.tables) == 18 and 'auth_users' not in Base.metadata.tables


def test_migration_refuses_non_auth_schema(database_auth):
    repo = database_auth[3]
    with repo.engine.begin() as connection:
        connection.execute(text('CREATE TABLE business_sentinel (id INTEGER PRIMARY KEY)'))
    with pytest.raises(AuthError, match='AUTH_MIGRATION_NONEMPTY_DATABASE'):
        repo.migrate()
    assert 'business_sentinel' in inspect(repo.engine).get_table_names()


def test_login_completion_cannot_clear_concurrent_temporary_lock(database_auth):
    import time
    repo, admin = database_auth[3:]
    version = repo.get(user_id=admin['id'])['auth_version']
    now = time.time()
    repo.failed(admin['id'], now, 1, 300)
    assert repo.successful(admin['id'], version, now) is False
    assert repo.get(user_id=admin['id'])['locked_until'] > now


def test_login_uses_database_not_environment_and_survives_restart(database_auth):
    with client(database_auth) as api:
        assert api.get('/api/v1/admin/users').status_code == 401
        assert login(api, database_auth[1]).status_code == 200
        identity = api.get('/api/v1/auth/me').json()
        assert identity['id'] == database_auth[4]['id'] and identity['roles'] == ['admin']
        cookies = dict(api.cookies)
        assert 'password_hash' not in api.get('/api/v1/admin/users').text
    with client(database_auth) as restarted:
        restarted.cookies.update(cookies)
        assert restarted.get('/api/v1/auth/me').status_code == 200


@pytest.mark.parametrize('state', ['wrong', 'unknown', 'locked', 'inactive'])
def test_login_rejections_are_generic(database_auth, state):
    if state in ('locked', 'inactive'):
        # Keep a second administrator to exercise lifecycle without violating the invariant.
        data, _, repo = database_auth, database_auth[4], database_auth[3]
        account, _ = repo.create('second@etl.test', 'Second', repo.get(user_id=data[4]['id'])['password_hash'], ['admin'])
        repo.change(account['id'], {'is_locked': True} if state == 'locked' else {'is_active': False})
        email = 'second@etl.test'
    else:
        email = 'missing@etl.test' if state == 'unknown' else 'admin@etl.test'
    with client(database_auth) as api:
        response = login(api, 'incorrect-test-password' if state == 'wrong' else database_auth[1], email)
        assert response.status_code == 401 and response.json()['detail']['code'] == 'AUTH_INVALID_CREDENTIALS'


def test_viewer_cannot_manage_users_or_index_or_choose_role(database_auth):
    make_user(database_auth)
    with client(database_auth) as api:
        assert login(api, database_auth[1], 'viewer@etl.test').status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'VIEWER'
        for path in ('/api/v1/admin/users', '/api/v1/system/health'):
            assert api.get(path).status_code == 403
        assert api.post('/api/v1/resource-versions/anything/index', headers=csrf(api), json={}).status_code == 403
        assert api.patch('/api/v1/admin/users/' + database_auth[4]['id'] + '/roles',
            headers=csrf(api), json={'roles': ['admin']}).status_code == 403
        assert api.get('/api/v1/resources?page=1&size=20&q=').json()['items'] == []


def test_operator_requires_explicit_capability(database_auth):
    account = make_user(database_auth, 'operator')
    with client(database_auth) as api:
        assert login(api, database_auth[1], 'operator@etl.test').status_code == 200
        assert api.get('/api/v1/master-data/sync/status').status_code == 403
        database_auth[3].change(account['id'], {'permissions': ['sync']})
        assert api.get('/api/v1/auth/me').status_code == 401
        assert login(api, database_auth[1], 'operator@etl.test').status_code == 200
        assert api.get('/api/v1/master-data/sync/status').status_code == 200
        assert api.get('/api/v1/admin/users').status_code == 403


def test_admin_create_change_password_and_session_revocation(database_auth):
    first, second = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    with client(database_auth) as admin_api, client(database_auth) as viewer:
        assert login(admin_api, database_auth[1]).status_code == 200
        response = admin_api.post('/api/v1/admin/users', headers=csrf(admin_api), json={
            'email': ' NEW@ETL.TEST ', 'display_name': 'New account', 'password': first, 'roles': ['viewer']})
        assert response.status_code == 201 and 'password_hash' not in response.text
        assert response.json()['email'] == 'new@etl.test'
        assert login(viewer, first, 'new@etl.test').status_code == 200
        assert viewer.get('/api/v1/auth/me').json()['must_change_password']
        assert viewer.post('/api/v1/search', headers=csrf(viewer), json={}).status_code == 403
        assert viewer.post('/api/v1/auth/change-password', headers=csrf(viewer),
            json={'old_password': first, 'new_password': second}).status_code == 200
        assert viewer.get('/api/v1/auth/me').status_code == 401
        assert login(viewer, second, 'new@etl.test').status_code == 200
        assert not viewer.get('/api/v1/auth/me').json()['must_change_password']
        identifier = response.json()['id']
        assert admin_api.patch('/api/v1/admin/users/' + identifier, headers=csrf(admin_api), json={'is_locked': True}).status_code == 200
        assert viewer.get('/api/v1/auth/me').status_code == 401


def test_last_admin_and_concurrent_demotion(database_auth):
    repo, admin = database_auth[3:]
    with pytest.raises(AuthError, match='AUTH_LAST_ADMIN_REQUIRED'):
        repo.change(admin['id'], {'is_active': False})
    second, _ = repo.create('second@etl.test', 'Second', repo.get(user_id=admin['id'])['password_hash'], ['admin'])
    def demote(identifier):
        try:
            repo.change(identifier, {}, role_codes=['viewer'])
            return 'changed'
        except AuthError as exc:
            return exc.code
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(demote, [admin['id'], second['id']]))
    assert sorted(results) == ['AUTH_LAST_ADMIN_REQUIRED', 'changed']


def test_admin_password_reset_revokes_session_and_forces_change(database_auth):
    account = make_user(database_auth)
    replacement = secrets.token_urlsafe(24)
    with client(database_auth) as admin_api, client(database_auth) as viewer:
        assert login(admin_api, database_auth[1]).status_code == 200
        assert login(viewer, database_auth[1], account['email']).status_code == 200
        path = '/api/v1/admin/users/' + account['id'] + '/reset-password'
        response = admin_api.post(path, headers=csrf(admin_api), json={'password': replacement})
        assert response.status_code == 200 and 'password_hash' not in response.text
        assert viewer.get('/api/v1/auth/me').status_code == 401
        assert login(viewer, database_auth[1], account['email']).status_code == 401
        assert login(viewer, replacement, account['email']).status_code == 200
        assert viewer.get('/api/v1/auth/me').json()['must_change_password'] is True
        assert viewer.get('/api/v1/resources').status_code == 403


def test_csrf_rate_logout_expiry_and_outage(database_auth, monkeypatch):
    with client(database_auth) as api:
        assert api.post('/api/v1/auth/login', json={'email': 'admin@etl.test', 'password': database_auth[1]}).status_code == 403
        assert login(api, database_auth[1]).status_code == 200
        auth = api.app.state.auth
        with monkeypatch.context() as patch:
            patch.setattr(auth, 'clock', lambda: 10**12)
            assert api.get('/api/v1/auth/me').status_code == 401
    # A database read failure must never produce an admin fallback.
    with client(database_auth) as api:
        assert login(api, database_auth[1]).status_code == 200
        monkeypatch.setattr(database_auth[3], 'get', lambda **kw: (_ for _ in ()).throw(AuthError('AUTH_DATABASE_UNAVAILABLE', 503)))
        assert api.get('/api/v1/auth/me').status_code == 503


def test_session_logout_and_account_rate_limit(database_auth):
    with client(database_auth) as api:
        assert login(api, database_auth[1]).status_code == 200
        assert api.post('/api/v1/auth/logout', headers=csrf(api)).status_code == 204
        assert api.get('/api/v1/auth/me').status_code == 401
        results = [login(api, 'wrong-test-password', 'missing@etl.test').status_code for _ in range(7)]
        assert results == [401] * 6 + [429]


def test_source_identity_does_not_bootstrap_auth_account(database_auth):
    container, _, _, repo, admin = database_auth
    container.sync.run(full=True)
    with container.uow() as business:
        source_user = business.find('master_users', {})[0]
        business.update('master_users', source_user['id'], {'hashed_password': repo.get(user_id=admin['id'])['password_hash']})
    with client(database_auth) as api:
        assert login(api, database_auth[1], source_user['email']).status_code == 401
        items, total = database_auth[3].list()
        assert total == 1 and items[0]['email'] == 'admin@etl.test'


def test_database_mode_bypass_is_rejected(database_auth):
    with pytest.raises(ValueError, match='AUTH_BYPASS_FORBIDDEN'):
        validate_mode(database_auth[0].settings.model_copy(update={'auth_dev_admin_bypass': True}))


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_granted_viewer_search_context_and_revocation(searchable, database_auth, method):
    container, version = searchable
    assert container is database_auth[0]
    user = make_user(database_auth, resource_ids=[version['resource_id']])
    with client(database_auth) as api:
        assert login(api, database_auth[1], 'viewer@etl.test').status_code == 200
        response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
        assert response.status_code == 200 and response.json()['total'] == 1
        assert response.json()['access_scope'] == 'authenticated_user'
        context_id = response.json()['items'][0]['context_id']
        assert api.get('/api/v1/chunks/' + context_id + '/context').status_code == 200
        database_auth[3].change(user['id'], {'resource_ids': []})
        assert api.get('/api/v1/chunks/' + context_id + '/context').status_code == 401
        assert login(api, database_auth[1], 'viewer@etl.test').status_code == 200
        assert api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method}).json()['total'] == 0
        assert api.get('/api/v1/chunks/' + context_id + '/context').status_code == 404


def test_source_file_requires_explicit_grant_and_retains_reference_only(source_viewer, database_auth):
    container, version, _, _ = source_viewer
    assert container is database_auth[0]
    user = make_user(database_auth)
    def stored_files():
        return {str(p.relative_to(container.storage.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in container.storage.root.rglob('*') if p.is_file()}
    before = stored_files()
    path = '/api/v1/resource-versions/' + version['id'] + '/file'
    with client(database_auth) as api:
        assert login(api, database_auth[1], 'viewer@etl.test').status_code == 200
        assert api.get(path).status_code == 403
        database_auth[3].change(user['id'], {'resource_ids': [version['resource_id']]})
        assert login(api, database_auth[1], 'viewer@etl.test').status_code == 200
        response = api.get(path)
        assert response.status_code == 200 and response.content.startswith(b'%PDF')
        assert not list(container.settings.source_temp_root.glob('attempt-*'))
        assert stored_files() == before  # Existing historical copies are preserved; no new copy is made.


def test_stale_admin_cannot_write_after_concurrent_revocation(database_auth):
    with client(database_auth) as api:
        assert login(api, database_auth[1]).status_code == 200
        from starlette.requests import Request
        # Capture a server principal before another administration operation changes it.
        token = api.cookies.get('etl_session')
        scope = {'type': 'http', 'headers': [(b'cookie', ('etl_session=' + token).encode())]}
        actor = api.app.state.auth.principal(Request(scope))
        repo = database_auth[3]
        target = make_user(database_auth)
        repo.change(actor.id, {'display_name': 'Changed elsewhere'})
        with pytest.raises(AuthError, match='AUTH_FORBIDDEN'):
            repo.change(target['id'], {'is_active': False}, actor=actor)
        assert repo.get(user_id=target['id'])['is_active']


def test_operator_evaluation_owns_dataset_and_run(evaluation, database_auth):
    container, version, command = evaluation
    assert container is database_auth[0]
    make_user(database_auth, 'operator', permissions=['evaluation'], resource_ids=[version['resource_id']])
    repo, admin = database_auth[3:]
    other, _ = repo.create('other@etl.test', 'Other', repo.get(user_id=admin['id'])['password_hash'], ['operator'],
                          permissions=['evaluation'], resource_ids=[version['resource_id']])
    repo.change(other['id'], {'must_change_password': False})
    with client(database_auth) as first, client(database_auth) as second:
        assert login(first, database_auth[1], 'operator@etl.test').status_code == 200
        assert login(second, database_auth[1], 'other@etl.test').status_code == 200
        created = first.post('/api/v1/evaluations/datasets', headers=csrf(first), json=command)
        assert created.status_code == 201
        dataset_id = created.json()['dataset_id']
        assert second.post('/api/v1/evaluations/runs', headers=csrf(second), json={'dataset_id': dataset_id, 'k_values': [1]}).status_code == 403
        run = first.post('/api/v1/evaluations/runs', headers=csrf(first), json={'dataset_id': dataset_id, 'k_values': [1]})
        assert run.status_code == 201
        identifier = run.json()['run_id']
        assert first.get('/api/v1/evaluations/runs/' + identifier).status_code == 200
        assert second.get('/api/v1/evaluations/runs/' + identifier).status_code == 403
        assert second.get('/api/v1/evaluations/runs').json()['items'] == []
