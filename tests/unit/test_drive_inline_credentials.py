import json
from contextlib import nullcontext

import pytest

from data_sync_etl.adapters.storage.source_google_drive import SCOPE, SourceGoogleDriveFileReader
from data_sync_etl.config import Settings
from data_sync_etl.domain.core import SourceFileError


@pytest.mark.parametrize('kind', ['service_account', 'authorized_user'])
def test_inline_drive_credentials_keep_readonly_scope_without_a_file(monkeypatch, kind):
    transport = pytest.importorskip('google.auth.transport.requests')
    service = pytest.importorskip('google.oauth2.service_account')
    user = pytest.importorskip('google.oauth2.credentials')
    payload = {'type': kind, 'private_test_value': 'never-log-this-test-value'}
    settings = Settings(_env_file=None, source_drive_enabled=True,
                        source_drive_auth_type=kind, source_drive_buckets={'DRIVE': 'root'},
                        source_drive_credentials_json=json.dumps(payload))
    assert 'never-log-this-test-value' not in repr(settings)
    calls = []
    credential, session = object(), object()

    def credentials_from_info(info, scopes):
        calls.append((info, scopes))
        return credential

    klass, method = ((service.Credentials, 'from_service_account_info') if kind == 'service_account'
                     else (user.Credentials, 'from_authorized_user_info'))
    monkeypatch.setattr(klass, method, credentials_from_info)
    monkeypatch.setattr(transport, 'AuthorizedSession', lambda value, **kw: nullcontext(session))
    reader = SourceGoogleDriveFileReader(settings)
    reader.configuration_check()
    with reader._session() as actual:
        assert actual is session
    assert calls == [(payload, [SCOPE])]


def test_inline_drive_credentials_reject_foreign_token_endpoint_without_echo():
    payload = json.dumps({'type': 'authorized_user', 'token_uri': 'https://foreign.example/token',
                          'refresh_token': 'never-log-this-test-refresh-token'})
    settings = Settings(_env_file=None, source_drive_enabled=True,
                        source_drive_auth_type='authorized_user', source_drive_buckets={'DRIVE': 'root'},
                        source_drive_credentials_json=payload)
    with pytest.raises(SourceFileError, match='DRIVE_CREDENTIALS_INVALID') as caught:
        with SourceGoogleDriveFileReader(settings)._session():
            pytest.fail('Unapproved token endpoint accepted')
    assert 'never-log-this' not in str(caught.value)
