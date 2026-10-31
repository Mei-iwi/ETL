"""Real ASGI/session/CSRF tests with isolated SQLite and explicit fake writers."""

import pytest

from data_sync_etl.domain.core import now
from tests.unit.test_master_auth import encoded
from tests.unit.test_master_auth import login as master_login
from tests.unit.test_master_auth import master_auth as master_auth
from tests.unit.test_single_admin_auth import (
    configured as configured,
)
from tests.unit.test_single_admin_auth import (
    credentials as credentials,
)
from tests.unit.test_single_admin_auth import (
    csrf,
    login,
    make_client,
)
from tests.unit.test_source_metadata_writeback import prepare_writeback
from tests.unit.test_source_resolution import seed_source_mapping


def test_http_writeback_requires_login_csrf_two_steps_and_strict_fields(configured):
    c, password, _ = configured
    resource, writer = prepare_writeback(c)
    c.source_writeback.permission = lambda **grant: grant['actor_id'] == 'single-admin'
    payload = dict(title='API title', expected_source_state_hash=resource['source_state_hash'],
                   idempotency_key='api-idempotency-01')
    with make_client(configured) as api:
        assert api.get('/api/v1/source-writeback/status').status_code == 401
        assert login(api, password).status_code == 200
        assert api.get('/api/v1/source-writeback/status').json()['writer_supported']
        path = '/api/v1/resources/resource-1/source-writeback'
        assert api.post(path, json=payload).status_code == 403
        approved = api.post(path, json={**payload, 'approve': True}, headers=csrf(api))
        assert approved.status_code == 409
        for malicious in ({'publication_status': 'DRAFT'}, {'sql': 'UPDATE secrets'}, {'source_record_key': 'foreign'}):
            assert api.post(path, json={**payload, **malicious}, headers=csrf(api)).status_code == 422
        pending = api.post(path, json=payload, headers=csrf(api))
        assert pending.status_code == 200 and pending.json()['status'] == 'WRITEBACK_PENDING_APPROVAL'
        assert not writer._replays
        result = api.post(path, json={**payload, 'approve': True}, headers=csrf(api))
        assert result.status_code == 200, result.text
        assert result.json()['status'] == 'WRITEBACK_APPLIED'
        assert api.post(path, json={**payload, 'approve': True}, headers=csrf(api)).json() == result.json()
        assert len(writer._replays) == 1
        assert not api.get('/api/v1/reconciliation/audit-jobs').json()['items']
        for secret in ('source_record_key', 'storage_bucket', 'storage_object_key', 'postgresql://'):
            assert secret not in result.text


def test_viewer_can_resolve_published_metadata_but_cannot_write(master_auth, monkeypatch):
    c, password, _ = master_auth
    c.settings.source_reference_enabled = True
    source = seed_source_mapping(c)
    with c.uow() as repo:
        repo.update('master_users', 'user-1', {'email': 'master@etl.test',
            'hashed_password': encoded(password), 'role_code': 'USER',
            'source_origin': 'POSTGRESQL', 'email_verified_at': now()})
        repo.update('master_learning_resources', 'resource-1', {'created_by': 'another-user'})
    monkeypatch.setattr(c.source, 'get_version', lambda _: source, raising=False)
    with make_client(master_auth) as api:
        assert master_login(api, master_auth).status_code == 200
        response = api.get('/api/v1/resources/resource-1/source-resolution')
        assert response.status_code == 200, response.text
        assert response.json()['content_availability'] == 'metadata-only'
        assert response.json()['source_record_available']
        assert api.get('/api/v1/resources/resource-1').status_code == 200
        versions = api.get('/api/v1/resources/resource-1/versions')
        assert versions.status_code == 200 and len(versions.json()) == 1
        version_id = versions.json()[0]['id']
        status = api.get(f'/api/v1/resource-versions/{version_id}/index-status?include_bags=false')
        assert status.status_code == 200 and status.json()['metadata_only']
        assert api.get(f'/api/v1/resource-versions/{version_id}/file').status_code in (403, 404, 409, 503)
        assert api.get('/api/v1/resources/resource-1/source-writeback/status').status_code == 403
        assert api.post('/api/v1/resources/resource-1/source-writeback', headers=csrf(api), json={}).status_code == 403
        with c.uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'publication_status': 'DRAFT'})
        assert api.get('/api/v1/resources/resource-1/source-resolution').status_code == 404


@pytest.mark.parametrize('path', ['/api/v1/resource-versions/{id}/file', '/api/v1/resource-versions/{id}/file?download=true',
                                  '/api/v1/resource-versions/{id}/source-info'])
def test_metadata_only_file_apis_deny_without_opening_bytes(configured, monkeypatch, path):
    c, password, _ = configured
    seed_source_mapping(c)
    with c.uow() as repo:
        version, = repo.find('resource_versions', {'resource_id': 'resource-1'})
    def forbidden(*args, **kwargs):
        pytest.fail('Metadata-only must not open source bytes')
    monkeypatch.setattr(c.source_files, 'open_stream', forbidden)
    with make_client(configured) as api:
        assert login(api, password).status_code == 200
        response = api.get(path.format(id=version['id']))
        assert response.status_code in (404, 409, 503), response.text
        if response.status_code == 503:
            assert response.json()['detail']['code'] == 'SOURCE_REFERENCE_DISABLED'
