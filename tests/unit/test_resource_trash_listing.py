"""Admin trash listing and revision conflicts through the actual HTTP boundary."""
from dataclasses import replace

import pytest

from tests.unit.test_resource_soft_delete import deletion
from tests.unit.test_search import searchable as searchable
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_single_admin_auth import csrf, login, make_client

URL = '/api/v1/resources?page=1&size=7&q='


def test_trash_delete_restore_round_trip_and_stale_revision(configured, searchable):
    with make_client(configured) as api:
        assert api.get(URL + '&deleted_only=true').status_code == 401
        assert login(api, configured[1]).status_code == 200
        row = next(row for row in api.get(URL).json()['items'] if row['id'] == 'resource-1')
        assert row['deletion_revision'] == 0 and row['master_deleted_at'] is None
        assert api.get('/api/v1/resources/resource-1').json()['deletion_revision'] == 0
        assert api.delete('/api/v1/resources/resource-1').status_code == 403  # CSRF
        response = api.request('DELETE', '/api/v1/resources/resource-1', headers=csrf(api),
            json={'expected_revision': 0, 'reason': 'Synthetic UI test'})
        assert response.status_code == 200
        assert all(row['id'] != 'resource-1' for row in api.get(URL).json()['items'])
        response = api.get(URL + '&deleted_only=true')
        assert response.status_code == 200
        trash = response.json()
        assert trash['pagination']['total'] == 1
        assert trash['items'][0]['id'] == 'resource-1'
        assert trash['items'][0]['master_deleted_at']
        revision = trash['items'][0]['deletion_revision']
        assert revision == 1
        assert api.get(URL + '&deleted_only=true&q=NO_SUCH_TITLE').json()['items'] == []
        response = api.post('/api/v1/resources/resource-1/restore', headers=csrf(api),
            json={'expected_revision': 0})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'RESOURCE_DELETION_CONFLICT'
        response = api.post('/api/v1/resources/resource-1/restore', headers=csrf(api),
            json={'expected_revision': revision})
        assert response.status_code == 200
        assert api.get(URL + '&deleted_only=true').json()['items'] == []
        assert any(row['id'] == 'resource-1' for row in api.get(URL).json()['items'])
        assert api.get('/api/v1/resources/resource-1').json()['deletion_revision'] == 2


@pytest.mark.parametrize('role,enabled', [('VIEWER', True), ('OPERATOR', True), ('ADMIN', False)])
def test_trash_listing_fails_closed_for_non_admin_or_disabled(configured, searchable, monkeypatch, role, enabled):
    c, _ = searchable
    deletion(c)
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        principal = api.app.state.auth.principal
        monkeypatch.setattr(api.app.state.auth, 'principal', lambda *args, **kwargs:
            replace(principal(*args, **kwargs), role=role))
        c.settings.admin_enabled = enabled
        response = api.get(URL + '&deleted_only=true')
        assert response.status_code == 403
        assert 'Synthetic' not in response.text


def test_trash_includes_source_restricted_items_without_allowing_restore(configured, searchable):
    c, _ = searchable
    deletion(c)
    with c.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'is_active': False})
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        assert api.get(URL + '&deleted_only=true').json()['pagination']['total'] == 1
        response = api.post('/api/v1/resources/resource-1/restore', headers=csrf(api),
            json={'expected_revision': 1})
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'RESOURCE_RESTORE_SOURCE_RESTRICTED'
