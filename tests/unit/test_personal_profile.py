from unittest.mock import MagicMock

import pytest
from pymongo.errors import AutoReconnect

from data_sync_etl.adapters.mongo.foundation_store import ProfileConflict, user_profile_revision
from data_sync_etl.domain.core import now
from data_sync_etl.domain.mongo_documents import UserProfileDocument
from tests.unit.test_master_auth import configured as configured
from tests.unit.test_master_auth import credentials as credentials
from tests.unit.test_master_auth import login
from tests.unit.test_master_auth import master_auth as master_auth
from tests.unit.test_single_admin_auth import csrf, make_client


@pytest.fixture
def personal(master_auth):
    container = master_auth[0]
    with container.uow() as repo:
        repo.update('master_users', 'user-1', {'mongo_id': 'profile-1'})
    metadata = MagicMock()
    document = UserProfileDocument(_id='profile-1', created_at=now(), updated_at=now(),
        organization_name='Trường học', address='Hà Nội', bio='Giảng viên')
    metadata.get_user.return_value = document
    container.metadata_documents = metadata
    return master_auth, metadata, document


def test_profile_requires_session_and_uses_only_own_explicit_mapping(personal):
    configured, metadata, _ = personal
    with make_client(configured) as api:
        assert api.get('/api/v1/auth/profile').status_code == 401
        assert login(api, configured).status_code == 200
        response = api.get('/api/v1/auth/profile')
        assert response.status_code == 200
        profile = response.json()
        assert profile['email'] == 'master@etl.test' and profile['organization_name'] == 'Trường học'
        assert profile['editable'] and len(profile['revision']) == 64
        metadata.get_user.assert_called_with('profile-1')
        assert not any(key in str(profile) for key in ['hashed_password', 'source_state_hash', 'external_fingerprint'])
        assert api.get('/api/v1/auth/profile/other-user').status_code in [403, 404]


def test_profile_update_csrf_allowlist_and_mass_assignment_protection(personal):
    configured, metadata, document = personal
    with make_client(configured) as api:
        login(api, configured)
        revision = user_profile_revision(document)
        assert api.patch('/api/v1/auth/profile', json={'revision': revision, 'bio': 'New'}).status_code == 403
        for field in ['email', 'full_name', 'role', 'subject_id', 'mongo_id', 'user_id', 'is_active']:
            response = api.patch('/api/v1/auth/profile', headers=csrf(api), json={
                'revision': revision, 'bio': 'New', field: 'another-user'})
            assert response.status_code == 422
        metadata.update_personal_profile.assert_not_called()
        response = api.patch('/api/v1/auth/profile', headers=csrf(api), json={
            'revision': revision, 'bio': '  Cập nhật  ', 'address': ''})
        assert response.status_code == 200
        metadata.update_personal_profile.assert_called_once_with('profile-1', {'bio': 'Cập nhật', 'address': None}, revision)
        assert api.get('/api/v1/auth/me').status_code == 200


def test_unlinked_admin_never_joins_master_by_email(personal):
    configured, metadata, _ = personal
    with make_client(configured) as api:
        assert login(api, configured, email='admin@etl.test').status_code == 200
        result = api.get('/api/v1/auth/profile').json()
        assert not result['editable'] and result['unavailable_reason'] == 'PROFILE_NOT_LINKED'
        assert result['email'] == 'admin@etl.test'
        assert api.patch('/api/v1/auth/profile', headers=csrf(api), json={'revision': 'missing', 'bio': 'New'}).status_code == 403
        metadata.get_user.assert_not_called()


def test_profile_conflict_and_mongo_failure_are_safe(personal):
    configured, metadata, document = personal
    with make_client(configured) as api:
        login(api, configured)
        metadata.update_personal_profile.side_effect = ProfileConflict('PRIVATE_INTERNAL')
        response = api.patch('/api/v1/auth/profile', headers=csrf(api), json={
            'revision': user_profile_revision(document), 'bio': 'New'})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'PROFILE_CHANGED'
        metadata.get_user.side_effect = AutoReconnect('PRIVATE_INTERNAL')
        response = api.get('/api/v1/auth/profile')
        assert response.status_code == 503 and 'PRIVATE_INTERNAL' not in response.text


def test_missing_master_mongo_id_is_not_inferred(personal):
    configured, metadata, _ = personal
    with configured[0].uow() as repo:
        repo.update('master_users', 'user-1', {'mongo_id': None})
    with make_client(configured) as api:
        login(api, configured)
        assert api.get('/api/v1/auth/profile').json()['editable'] is False
        metadata.get_user.assert_not_called()


def test_own_profile_does_not_require_admin_feature(personal):
    configured, metadata, document = personal
    configured[0].settings.admin_enabled = False
    with make_client(configured) as api:
        assert login(api, configured).status_code == 200
        assert api.get('/api/v1/auth/profile').status_code == 200
        response = api.patch('/api/v1/auth/profile', headers=csrf(api), json={
            'revision': user_profile_revision(document), 'organization_name': 'Đơn vị mới'})
        assert response.status_code == 200
        assert api.get('/api/v1/admin/users').status_code == 403
        metadata.update_personal_profile.assert_called_once()
