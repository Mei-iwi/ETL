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
            'role_code': 'USER'})
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


def test_automatic_login_keeps_regular_users_separate_from_configured_admin(master_auth):
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


def test_synced_admin_gets_admin_capabilities_and_loses_them_on_role_change(master_auth):
    container = master_auth[0]
    with container.uow() as repo:
        repo.update('master_users', 'user-1', {'role_code': 'ADMIN'})
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        me = api.get('/api/v1/auth/me').json()
        assert me['id'] == 'master:user-1' and me['role'] == 'ADMIN' and me['roles'] == ['admin']
        assert set(me['permissions']) == {'sync', 'process', 'index', 'evaluation'}
        assert api.get('/api/v1/system/health').status_code == 200
        assert api.get('/api/v1/resources?page=1&size=100&q=').json()['pagination']['total'] > 1
        # Capability checks allow all admin routes; domain validation still runs.
        from types import SimpleNamespace

        from data_sync_etl.security.authorization import authorize

        principal = api.app.state.auth.principal(SimpleNamespace(cookies=api.cookies))
        for path in ['/api/v1/master-data/subjects', '/api/v1/resource-versions/any/index',
                     '/api/v1/resource-versions/any/process', '/api/v1/evaluations/runs']:
            authorize(SimpleNamespace(method='POST', url=SimpleNamespace(path=path)), principal)
        container.settings.admin_enabled = False
        assert api.get('/api/v1/auth/me').json()['role'] == 'VIEWER'
        assert api.get('/api/v1/system/health').status_code == 403
        container.settings.admin_enabled = True
        assert api.get('/api/v1/auth/me').json()['role'] == 'ADMIN'
        with container.uow() as repo:
            repo.update('master_users', 'user-1', {'role_code': 'USER'})
        assert api.get('/api/v1/auth/me').status_code == 401
        assert login(api, master_auth).status_code == 200
        assert api.get('/api/v1/auth/me').json()['role'] == 'VIEWER'


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
def test_master_viewer_search_and_source_file_ownership(source_viewer, master_auth, method):
    container, version, _, _ = source_viewer
    assert container is master_auth[0]
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-1'})
    container.settings.auth_master_resource_grants = {'user-1': [version['resource_id']]}
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in container.storage.root.rglob('*') if p.is_file()}
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
        assert response.status_code == 200 and response.json()['total'] == 1
        assert response.json()['access_scope'] == 'authenticated_user'
        path = '/api/v1/resource-versions/' + version['id'] + '/file'
        response = api.get(path)
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        with container.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-2'})
        assert api.get(path).status_code == 401
        assert login(api, master_auth).status_code == 200
        response = api.get(path)
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in container.storage.root.rglob('*') if p.is_file()}
    assert after == before


def test_master_owner_can_open_viewer_and_download_original(source_viewer, master_auth):
    container, version, _, source_path = source_viewer
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-1'})
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        resource_url = f'/api/v1/resources/{version["resource_id"]}'
        file_url = f'/api/v1/resource-versions/{version["id"]}/file'
        assert api.get(resource_url).status_code == 200
        assert any(row['id'] == version['id'] for row in api.get(resource_url + '/versions').json())
        readiness = api.get(file_url.replace('/file', '/index-status'))
        assert readiness.status_code == 200
        assert readiness.json()['processing_status'] == 'CONTENT_READY'
        assert readiness.json()['index_status'] == 'READY'
        source_info = api.get(file_url.replace('/file', '/source-info'))
        assert source_info.status_code == 409
        assert source_info.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        preview = api.get(file_url + '?view=preview')
        assert preview.status_code == 409
        assert preview.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        download = api.get(file_url + '?download=true')
        assert download.status_code == 409
        assert download.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        range_response = api.get(file_url, headers={'Range': 'bytes=0-3'})
        assert range_response.status_code == 409
        assert api.post(file_url.replace('/file', '/index'), headers=csrf(api), json={}).status_code == 403


@pytest.mark.parametrize('searchable', [{'unreviewed': True}], indirect=True)
def test_authenticated_reader_can_search_shared_unreviewed_file_without_management_rights(source_viewer, master_auth):
    container, version, _, source_path = source_viewer
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-1'})
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        url = f'/api/v1/resource-versions/{version["id"]}/file'
        preview = api.get(url + '?view=preview')
        assert preview.status_code == 409
        assert preview.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        download = api.get(url + '?download=true')
        assert download.status_code == 409
        assert download.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        source_info = api.get(url.replace('/file', '/source-info'))
        assert source_info.status_code == 409
        assert source_info.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        for method in ['bow', 'vector', 'hybrid']:
            response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
            assert response.status_code == 200 and response.json()['total'] == 1
            item = response.json()['items'][0]
            assert item['can_view_file'] is True
            assert api.get(item['context_url']).status_code == 200
        with container.uow() as repo:
            units = repo.find('content_units', {'resource_version_id': version['id']})
            assert all(row['review_status'] == 'UNREVIEWED' and not row['retrieval_eligible'] for row in units)
            repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-2'})
        assert login(api, master_auth).status_code == 200
        response = api.get(url)
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        for method in ['bow', 'vector', 'hybrid']:
            response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
            assert response.status_code == 200 and response.json()['total'] == 1
            assert response.json()['items'][0]['can_view_file'] is True
        assert api.get(item['context_url']).status_code == 200
        assert api.post(url.replace('/file', '/reindex'), headers=csrf(api), json={}).status_code == 403
        assert api.patch(f'/api/v1/resources/{version["resource_id"]}/description',
                         headers=csrf(api), json={'description': 'not owned'}).status_code == 403
        assert api.delete(f'/api/v1/resources/{version["resource_id"]}', headers=csrf(api)).status_code == 403


def test_public_viewer_gets_only_readable_versions_and_no_management_rights(source_viewer, master_auth):
    container, version, _, source_path = source_viewer
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-2'})
        repo.insert('resource_versions', {**version, 'id': 'private-later-version', 'mongo_id': None,
            'version_number': version['version_number'] + 1, 'lifecycle_status': 'DRAFT'})
    with make_client(master_auth) as api:
        resource_url = f'/api/v1/resources/{version["resource_id"]}'
        url = f'/api/v1/resource-versions/{version["id"]}/file'
        assert api.get(url).status_code == 401
        assert api.get(url.replace('/file', '/bilingual?include_items=false')).status_code == 401
        assert api.get(url.replace('/file', '/index-status?bag_limit=10')).status_code == 401
        assert login(api, master_auth).status_code == 200
        assert api.get(resource_url).status_code == 200
        rows = api.get(resource_url + '/versions')
        assert rows.status_code == 200 and [row['id'] for row in rows.json()] == [version['id']]
        preview = api.get(url + '?view=preview')
        assert preview.status_code == 409
        assert preview.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        download = api.get(url + '?download=true')
        assert download.status_code == 409
        assert download.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        source_info = api.get(url.replace('/file', '/source-info'))
        assert source_info.status_code == 409
        assert source_info.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        assert api.get(url.replace('/file', '/index-status')).status_code == 200
        assert api.get(url.replace('/file', '/index-status?bag_limit=10')).status_code == 200
        assert api.get(url.replace('/file', '/bilingual?include_items=false')).status_code == 403
        for suffix in ['chunks', 'content-units', 'etl-status']:
            assert api.get(url.replace('/file', '/' + suffix)).status_code == 403
        for suffix in ['process', 'postprocess', 'index', 'reindex', 'ocr-jobs']:
            assert api.post(url.replace('/file', '/' + suffix), headers=csrf(api), json={}).status_code == 403


@pytest.mark.parametrize('searchable', [{'structured': True, 'rejected_leaf': True}], indirect=True)
@pytest.mark.parametrize('owner_id', ['user-1', 'user-2'])
def test_public_file_denied_when_any_unit_is_restricted(source_viewer, master_auth, owner_id):
    container, version, _, _ = source_viewer
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': owner_id})
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        response = api.get(f'/api/v1/resource-versions/{version["id"]}/file')
        assert response.status_code in (403, 404, 409)
        if response.status_code == 409:
            assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        results = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': 'bow'}).json()
        assert all(item['can_view_file'] is False for item in results['items'])


def test_version_listing_requires_principal_in_authenticated_mode(master_auth):
    from types import SimpleNamespace

    from data_sync_etl.api.routes import list_resource_versions

    request = SimpleNamespace(state=SimpleNamespace(), app=SimpleNamespace(state=SimpleNamespace(container=master_auth[0])))
    with pytest.raises(AuthError, match='AUTH_LOGIN_REQUIRED'):
        list_resource_versions('resource-1', request)


def test_master_ownership_isolates_list_details_files_and_search_context(source_viewer, master_auth):
    container, version, _, _ = source_viewer
    with container.uow() as repo:
        resources = repo.find('master_learning_resources', {})
        other_id = next(row['id'] for row in resources if row['id'] != version['resource_id'])
        repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-1'})
        repo.update('master_learning_resources', other_id, {'created_by': 'user-2', 'is_active': True,
            'publication_status': 'PUBLISHED', 'deleted_at': None, 'source_missing_at': None})
        other_version = repo.insert('resource_versions', {**version, 'id': 'other-user-version',
            'resource_id': other_id, 'mongo_id': None})
        repo.update('master_users', 'user-2', {'email': 'other@etl.test', 'hashed_password': encoded(master_auth[1]),
            'source_origin': 'POSTGRESQL', 'source_missing_at': None, 'is_active': True,
            'deleted_at': None, 'email_verified_at': now(), 'role_is_active': True, 'role_deleted_at': None})
    # A legacy manual grant must not allow user-1 to see user-2's document.
    container.settings.auth_master_resource_grants = {'user-1': [other_id]}
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        result = api.get('/api/v1/resources?page=1&size=1&q=').json()
        assert result['pagination']['total'] == 1 and result['items'][0]['id'] == version['resource_id']
        assert api.get(f'/api/v1/resources/{version["resource_id"]}').status_code == 200
        assert api.get(f'/api/v1/resources/{other_id}').status_code == 200
        assert api.get(f'/api/v1/resources/{other_id}/versions').status_code == 200
        # Published learning material is readable by authenticated users for
        # search/navigation metadata, while SOURCE original-file operations
        # remain provider-owned and fail closed for a non-owner.
        for suffix in ['file', 'file?view=preview', 'file?view=text', 'source-info']:
            response = api.get(f'/api/v1/resource-versions/{other_version["id"]}/{suffix}')
            assert response.status_code in (403, 404, 409)
            if response.status_code == 409:
                assert response.json()['detail']['code'] in {'SOURCE_OPEN_UNSUPPORTED', 'FILE_NOT_READY'}
        # Chunk/content and processing-status endpoints retain the existing
        # owner-scoped ACL; publication does not grant management visibility.
        for suffix in ['chunks', 'content-units', 'etl-status']:
            assert api.get(f'/api/v1/resource-versions/{other_version["id"]}/{suffix}').status_code in (403, 404, 409)
        # Index readiness is public metadata used by search navigation.
        assert api.get(f'/api/v1/resource-versions/{other_version["id"]}/index-status').status_code == 200
        result = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': 'bow'}).json()
        assert result['total'] == 1
        context = result['items'][0]['context_url']
        assert api.get(context).status_code == 200
        assert login(api, master_auth, email='other@etl.test').status_code == 200
        result = api.get('/api/v1/resources?page=1&size=10&q=').json()
        assert result['pagination']['total'] == 1 and result['items'][0]['id'] == other_id
        assert api.get(f'/api/v1/resources/{version["resource_id"]}').status_code == 200
        response = api.get(f'/api/v1/resource-versions/{version["id"]}/file')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        assert api.get(context).status_code == 200  # Published shared search remains readable.
        for method in ['bow', 'vector', 'hybrid']:
            result = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
            assert result.status_code == 200 and result.json()['total'] == 1
        assert login(api, master_auth, email='admin@etl.test').status_code == 200
        assert api.get('/api/v1/resources?page=1&size=100&q=').json()['pagination']['total'] > 1
        assert api.get(f'/api/v1/resources/{version["resource_id"]}').status_code == 200


def test_master_without_owner_has_no_access_even_with_manual_grant(source_viewer, master_auth):
    container, version, _, _ = source_viewer
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': None})
    container.settings.auth_master_resource_grants = {'user-1': [version['resource_id']]}
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        assert api.get('/api/v1/resources?page=1&size=10&q=').json()['pagination']['total'] == 0
        response = api.get(f'/api/v1/resource-versions/{version["id"]}/file')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        assert api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': 'bow'}).json()['total'] == 1


def test_live_owner_check_rejects_stale_or_expanded_resource_ids(source_viewer, master_auth):
    from types import SimpleNamespace

    from data_sync_etl.domain.search_access import SearchAccess
    from data_sync_etl.security.auth import Principal
    from data_sync_etl.security.authorization import authorize

    container, version, _, _ = source_viewer
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-2'})
        resource = repo.get('master_learning_resources', version['resource_id'])
        unit = repo.find('content_units', {'resource_version_id': version['id']})[0]
        # Explicit ownership/grants must not authorize a now-private/draft file.
        repo.update('master_learning_resources', version['resource_id'], {'publication_status': 'DRAFT'})
    principal = Principal('master:user-1', 'VIEWER', 'master_users', 'master-readonly',
        resource_ids=(version['resource_id'],))
    request = SimpleNamespace(method='GET', app=SimpleNamespace(state=SimpleNamespace(container=container)))
    for path in [f'/api/v1/resources/{version["resource_id"]}', f'/api/v1/resource-versions/{version["id"]}/file']:
        request.url = SimpleNamespace(path=path)
        with pytest.raises(AuthError):
            authorize(request, principal)
    access = SearchAccess('authenticated_user', frozenset([version['resource_id']]), principal.id, owner_id='user-1')
    assert access.evaluate(resource, version, unit) == 'RESOURCE_NOT_OWNED'


@pytest.mark.parametrize('restriction', ['draft', 'inactive', 'unreviewed', 'rejected', 'ineligible'])
def test_shared_search_revokes_context_when_content_is_no_longer_public(source_viewer, master_auth, restriction):
    container, version, _, _ = source_viewer
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'created_by': 'user-2'})
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        result = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': 'bow'}).json()
        assert result['total'] == 1
        context = result['items'][0]['context_url']
        assert api.get(context).status_code == 200
        response = api.get(f'/api/v1/resource-versions/{version["id"]}/file')
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        with container.uow() as repo:
            if restriction in ('draft', 'inactive'):
                repo.update('master_learning_resources', version['resource_id'],
                    {'publication_status': 'DRAFT'} if restriction == 'draft' else {'is_active': False})
            else:
                for unit in repo.find('content_units', {'resource_version_id': version['id']}):
                    repo.update('content_units', unit['id'], {'retrieval_eligible': False} if restriction == 'ineligible'
                        else {'review_status': 'UNREVIEWED' if restriction == 'unreviewed' else 'REJECTED'})
        assert api.get(context).status_code == 404
        response = api.get(f'/api/v1/resource-versions/{version["id"]}/file')
        assert response.status_code in (403, 404, 409)
        if response.status_code == 409:
            assert response.json()['detail']['code'] == 'SOURCE_OPEN_UNSUPPORTED'
        for method in ['bow', 'vector', 'hybrid']:
            response = api.post('/api/v1/search', headers=csrf(api), json={'query': 'page text', 'method': method})
            assert response.status_code == 200 and response.json()['total'] == 0
