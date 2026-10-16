import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import authorize_drive_readonly as auth


def client_file(tmp_path):
    client = tmp_path / 'desktop.json'
    client.write_text(json.dumps({'installed': {
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
    }}), encoding='utf-8')
    return client


def test_paths_outside_repository_and_refuse_overwrite(tmp_path):
    repository = tmp_path / 'repo'
    repository.mkdir()
    client = client_file(tmp_path)
    token = tmp_path / 'authorized.json'
    _, result = auth.validate_paths(client, token, repository=repository)
    assert result == token
    with pytest.raises(ValueError, match='CREDENTIAL_MUST_BE_OUTSIDE_REPOSITORY'):
        auth.validate_paths(client, repository / 'token.json', repository=repository)
    token.write_text('{}')
    with pytest.raises(ValueError, match='TOKEN_TARGET_NOT_NEW'):
        auth.validate_paths(client, token, repository=repository)
    assert token.read_text() == '{}'


def test_reject_non_google_token_endpoint(tmp_path):
    client = client_file(tmp_path)
    config = json.loads(client.read_text())
    config['installed']['token_uri'] = 'https://example.com/token'
    client.write_text(json.dumps(config))
    with pytest.raises(ValueError, match='DESKTOP_GOOGLE_CLIENT_REQUIRED'):
        auth.validate_paths(client, tmp_path / 'token.json', repository=tmp_path / 'repo')


@pytest.mark.parametrize('scopes,refresh,accepted', [
    ([auth.SCOPE], True, True), ([auth.SCOPE], False, False),
    (['https://www.googleapis.com/auth/drive'], True, False),
])
def test_oauth_pkce_loopback_readonly_no_secret_output(tmp_path, monkeypatch, capsys, scopes, refresh, accepted):
    flow_module = pytest.importorskip('google_auth_oauthlib.flow', reason='Optional Google OAuth runtime not installed')
    token = tmp_path / 'token.json'
    monkeypatch.setattr(auth, 'validate_paths', lambda *args: ({'installed': {}}, token))
    monkeypatch.setattr(auth.logging, 'disable', lambda _: None)
    save = MagicMock()
    monkeypatch.setattr(auth, 'save_private_token', save)
    credentials = SimpleNamespace(granted_scopes=scopes, scopes=scopes,
        refresh_token='fixture-not-a-real-token' if refresh else None,
        valid=True, refresh=MagicMock(), to_json=lambda: json.dumps({'token': 'fixture-private'}))
    flow = MagicMock()
    flow.run_local_server.return_value = credentials
    constructor = MagicMock(return_value=flow)
    monkeypatch.setattr(flow_module.InstalledAppFlow, 'from_client_config', constructor)
    if accepted:
        auth.authorize('unused', 'unused')
        assert save.call_args.args[1]['type'] == 'authorized_user'
        credentials.refresh.assert_called_once()
    else:
        with pytest.raises(ValueError, match='READONLY_REFRESH_CREDENTIAL_REQUIRED'):
            auth.authorize('unused', 'unused')
        save.assert_not_called()
    assert constructor.call_args.kwargs == {'scopes': [auth.SCOPE], 'autogenerate_code_verifier': True}
    options = flow.run_local_server.call_args.kwargs
    assert options['host'] == options['bind_addr'] == '127.0.0.1'
    assert options['port'] == 0 and options['timeout_seconds'] == 300
    assert options['authorization_prompt_message'] is None and options['include_granted_scopes'] == 'false'
    assert 'fixture-private' not in capsys.readouterr().out


def test_main_sanitizes_oauth_exception(monkeypatch, capsys, tmp_path):
    import sys
    monkeypatch.setattr(sys, 'argv', ['oauth', '--client-file', str(tmp_path / 'client'),
                                    '--token-file', str(tmp_path / 'token')])
    def fail(*args, **kwargs):
        raise RuntimeError('SECRET_TOKEN_CLIENT_SECRET_AUTH_CODE')
    monkeypatch.setattr(auth, 'authorize', fail)
    assert auth.main() == 1
    output = capsys.readouterr()
    assert 'SECRET_TOKEN' not in output.out + output.err and 'OAUTH_NOT_COMPLETED' in output.out
