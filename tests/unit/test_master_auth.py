import hashlib
import secrets

import pytest
from sqlalchemy import select

from data_sync_etl.db.models import MasterUser
from data_sync_etl.domain.core import now
from data_sync_etl.security.auth import AuthError, validate_mode
from data_sync_etl.security.master_service import (
    MasterAuthService,
    MasterIdentityReader,
    verify_pbkdf2,
)
from tests.unit.test_search import searchable as searchable
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_single_admin_auth import csrf, make_client
from tests.unit.test_source_reference import source_viewer as source_viewer


def encoded(password, salt='ab12' * 6, encoding='text'):
    raw = bytes.fromhex(salt) if encoding == 'hex' else salt.encode()
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), raw, 120000).hex()
    return f'pbkdf2_sha256$120000${salt}${digest}'


@pytest.fixture
def master_auth(configured, monkeypatch):
    container, password, files = configured
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update('master_users', 'user-1', {'email': 'master@etl.test', 'hashed_password': encoded(password),
            'source_origin': 'POSTGRESQL', 'source_missing_at': None, 'is_active': True,
            'deleted_at': None, 'email_verified_at': now(), 'role_is_active': True, 'role_deleted_at': None,
            'role_code': 'ADMIN'})
    container.settings = container.settings.model_copy(update={'auth_mode': 'master_users',
        'auth_master_resource_grants': {'user-1': ['resource-1']}})
    monkeypatch.setattr('data_sync_etl.security.auth.create_auth_service', lambda settings:
        MasterAuthService(settings, reader=MasterIdentityReader(settings, engine=container.engine)))
    return configured


def login(api, data, provider='master', email='master@etl.test', password=None):
    return api.post('/api/v1/auth/login', headers=csrf(api), json={
        'email': email, 'password': password or data[1], 'provider': provider})


@pytest.mark.parametrize('encoding', ['text', 'hex'])
def test_pbkdf2_known_vectors_and_bad_input(encoding):
    password = secrets.token_urlsafe(20)
    value = encoded(password, encoding=encoding)
    assert verify_pbkdf2(value, password, encoding)
    assert verify_pbkdf2(value, password, 'auto')
    assert not verify_pbkdf2(value, password + 'wrong', 'auto')
    assert not verify_pbkdf2(value, password + 'wrong', encoding)
    assert not verify_pbkdf2(value.replace('$120000$', '$999999999999$'), password, encoding)
    assert not verify_pbkdf2('DEMO_NON_AUTHENTICATING_HASH', password, encoding)


def test_automatic_login_and_no_source_admin_promotion(master_auth):
    with make_client(master_auth) as api:
        assert api.get('/api/v1/auth/config').json()['mode'] == 'master_users'
        assert login(api, master_auth).status_code == 200
        me = api.get('/api/v1/auth/me').json()
        assert me['role'] == 'VIEWER' and me['id'] == 'master:user-1'
        assert 'hashed_password' not in str(me) and 'fingerprint' not in str(me)
        assert api.get('/api/v1/system/health').status_code == 403
        assert api.get('/api/v1/admin/users').status_code == 403
        assert api.post('/api/v1/resource-versions/any/index', headers=csrf(api), json={}).status_code == 403
        assert login(api, master_auth, 'admin', 'admin@etl.test').status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'ADMIN'
        assert api.get('/api/v1/system/health').status_code == 200
        assert login(api, master_auth, 'master', 'admin@etl.test').status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'ADMIN'
        assert login(api, master_auth, 'admin', 'master@etl.test').status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'VIEWER'
        assert api.post('/api/v1/auth/login', headers=csrf(api), json={
            'email': 'master@etl.test', 'password': master_auth[1]}).status_code == 200


def test_admin_flag_blocks_configured_admin_and_preserves_boundary(master_auth):
    with make_client(master_auth) as api:
        assert login(api, master_auth, email='admin@etl.test').status_code == 200
        master_auth[0].settings.admin_enabled = False
        assert api.get('/api/v1/auth/me').status_code == 403
        assert login(api, master_auth, email='admin@etl.test').status_code == 401
        assert login(api, master_auth).status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'VIEWER'
        assert api.get('/api/v1/resources').status_code == 403
        assert api.post('/api/v1/resource-versions/any/index', headers=csrf(api), json={}).status_code == 403


def test_admin_disabled_does_not_require_admin_secret(master_auth, tmp_path):
    settings = master_auth[0].settings.model_copy(update={
        'admin_enabled': False, 'admin_password_hash_file': tmp_path / 'DO_NOT_READ',
        'auth_state_path': tmp_path / 'user-only-sessions.sqlite3',
    })
    service = MasterAuthService(settings, reader=MasterIdentityReader(settings, engine=master_auth[0].engine))
    assert service.password_hash == b''


def test_shared_email_uses_verified_credential_without_role_linking(master_auth):
    source_password = secrets.token_urlsafe(24)
    with master_auth[0].uow() as repo:
        repo.update('master_users', 'user-1', {'email': 'admin@etl.test', 'hashed_password': encoded(source_password, encoding='hex')})
    master_auth[0].settings.auth_master_pbkdf2_salt_encoding = 'auto'
    with make_client(master_auth) as api:
        assert login(api, master_auth, email='admin@etl.test', password=source_password).status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'VIEWER'
        assert login(api, master_auth, email='admin@etl.test').status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'ADMIN'


@pytest.mark.parametrize('changes', [
    {'is_active': False}, {'role_is_active': False}, {'deleted_at': now()},
    {'source_missing_at': now()}, {'role_deleted_at': now()}, {'email_verified_at': None},
    {'hashed_password': encoded('a-new-unknown-password')},
])
def test_status_hash_changes_revoke_existing_session(master_auth, changes):
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        with master_auth[0].uow() as repo:
            repo.update('master_users', 'user-1', changes)
        assert api.get('/api/v1/auth/me').status_code == 401
        assert login(api, master_auth).status_code == 401


def test_master_read_only_and_same_mode_restart(master_auth):
    def fingerprint():
        with master_auth[0].engine.connect() as c:
            return list(c.execute(select(MasterUser.__table__)).mappings())
    before = fingerprint()
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        assert api.post('/api/v1/auth/logout', headers=csrf(api)).status_code == 204
        assert api.get('/api/v1/auth/me').status_code == 401
        assert login(api, master_auth).status_code == 200
        cookies = dict(api.cookies)
    with make_client(master_auth) as api:
        api.cookies.update(cookies)
        assert api.get('/api/v1/auth/me').status_code == 200
    assert fingerprint() == before


def test_stale_master_denied_but_admin_explicitly_available(master_auth, monkeypatch):
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        auth = api.app.state.auth
        monkeypatch.setattr(auth.reader, 'read', lambda **_: (_ for _ in ()).throw(AuthError('AUTH_MASTER_STALE', 503)))
        assert api.get('/api/v1/auth/me').status_code == 503
        assert login(api, master_auth).status_code == 503
        assert login(api, master_auth, 'admin', 'admin@etl.test').status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'ADMIN'


def test_stale_watermark_is_not_freshness_and_ambiguous_email_denied(master_auth):
    c = master_auth[0]
    reader = MasterIdentityReader(c.settings, engine=c.engine)
    assert reader.read(email='master@etl.test') is not None
    with c.uow() as repo:
        repo.update('master_users', 'user-2', {'email': 'MASTER@ETL.TEST'})
    assert reader.read(email='master@etl.test') is None
    with pytest.raises(AuthError, match='AUTH_MASTER_STALE'):
        reader.read(user_id='user-1', now=now().timestamp() + 500)
    with pytest.raises(ValueError, match='AUTH_BYPASS_FORBIDDEN'):
        validate_mode(c.settings.model_copy(update={'auth_dev_admin_bypass': True}))


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_master_viewer_search_and_source_file_grants(source_viewer, master_auth, method):
    container, version, _, _ = source_viewer
    assert container is master_auth[0]
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': None})
    container.settings.auth_master_resource_grants = {'user-1': [version['resource_id']]}
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in container.storage.root.rglob('*') if p.is_file()}
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
        assert response.status_code == 200 and response.json()['total'] == 1
        assert response.json()['access_scope'] == 'authenticated_user'
        path = '/api/v1/resource-versions/' + version['id'] + '/file'
        response = api.get(path)
        assert response.status_code == 200 and response.content.startswith(b'%PDF')
        container.settings.auth_master_resource_grants = {}
        assert api.get(path).status_code == 401
        assert login(api, master_auth).status_code == 200
        assert api.get(path).status_code == 403
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in container.storage.root.rglob('*') if p.is_file()}
    assert after == before
