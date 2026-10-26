"""A temporary authority failure denies access without inventing session expiry."""
import pytest

from data_sync_etl.security.auth import AuthError
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_single_admin_auth import login, make_client


@pytest.mark.parametrize('code,status', [('AUTH_MASTER_STALE', 503), ('AUTH_MASTER_UNAVAILABLE', 503),
                                       ('AUTH_SESSION_EXPIRED', 401)])
def test_send_time_revalidation_preserves_actual_failure(configured, monkeypatch, code, status):
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        auth = api.app.state.auth
        original = auth.principal
        def unavailable(request, *, touch=True):
            if not touch:
                raise AuthError(code, status)
            return original(request, touch=touch)
        monkeypatch.setattr(auth, 'principal', unavailable)
        response = api.get('/api/v1/auth/me')
        assert response.status_code == status
        assert response.json()['detail']['code'] == code
