import json

import httpx
import pytest
from pydantic import ValidationError

from scripts.neo4j_search_canary import CanaryProfile, measure


def test_missing_password_blocks_before_network(tmp_path):
    p = CanaryProfile(email='test@example.org', password_file=tmp_path/'missing')
    result = measure(p, transport=httpx.MockTransport(lambda r: pytest.fail('No request permitted')))
    assert result['status'] == 'AUTH_BLOCKED'


@pytest.mark.parametrize('url', ['https://outside.example', 'http://user:secret@127.0.0.1',
                                'http://127.0.0.1/path', 'http://127.0.0.1:invalid'])
def test_canary_never_sends_credentials_outside_approved_loopback(url):
    with pytest.raises(ValidationError):
        CanaryProfile(email='test@example.org', password_file='private', base_url=url)


@pytest.mark.parametrize('mode', ['master_users', 'single_admin'])
def test_real_login_contract_and_no_secrets_in_result(tmp_path, mode):
    path = tmp_path/'password'
    path.write_text('test-only-private-password', encoding='utf-8')
    p = CanaryProfile(email='test@example.org', password_file=path)
    state = {'logged_in':False}

    def endpoint(request):
        url = request.url.path
        if url.endswith('/config'):
            return httpx.Response(200, json={'mode':mode})
        if url.endswith('/csrf'):
            return httpx.Response(200, json={'csrf_token':'test-only-csrf'})
        if url.endswith('/login'):
            body = json.loads(request.content)
            assert body['provider'] == 'master'
            assert body['password'] == 'test-only-private-password'
            state['logged_in'] = True
            return httpx.Response(200, json={'authenticated':True})
        if url.endswith('/me'):
            return httpx.Response(200, json={'mode':'master_users','role':'ADMIN',
                'id':'master:test-only', 'provenance':'master-readonly'})
        if url.endswith('/logout'):
            state['logged_in'] = False
            return httpx.Response(204)
        if url.endswith('/search'):
            return httpx.Response(200, json={'items':[],'total':0}) if state['logged_in'] else httpx.Response(401)
        pytest.fail('Unexpected endpoint')

    result = measure(p, transport=httpx.MockTransport(endpoint))
    assert result['status'] == ('AUTHENTICATED_CANARY_MEASURED' if mode == 'master_users' else 'AUTH_BLOCKED')
    assert not state['logged_in']
    assert 'test-only-private-password' not in json.dumps(result)
    assert 'test-only-csrf' not in json.dumps(result)
