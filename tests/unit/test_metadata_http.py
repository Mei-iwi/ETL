import pytest

from tests.unit import test_metadata_search, test_single_admin_auth

metadata = test_metadata_search.metadata
credentials = test_single_admin_auth.credentials
configured = test_single_admin_auth.configured


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_authenticated_http_metadata_modes(metadata, configured, method):
    c, _, _, service = metadata
    c.search.settings = service.settings = c.settings
    c.settings.search_metadata_enabled = True
    c.settings.search_metadata_content_enabled = False
    service.reconcile(apply=True, dense=method != 'bow')
    _, password, _ = configured
    with test_single_admin_auth.make_client(configured) as client:
        body = {'query': 'quy đồng', 'method': method}
        assert client.post('/api/v1/search', json=body, headers=test_single_admin_auth.csrf(client)).status_code == 401
        assert test_single_admin_auth.login(client, password).status_code == 200
        response = client.post('/api/v1/search', json=body, headers=test_single_admin_auth.csrf(client))
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload['total'] == 1 and payload['access_scope'] == 'authenticated_admin'
        item = payload['items'][0]
        assert item['match_origin'] == 'METADATA' and item['context_id'] is None
        assert item['resource_format'] == 'mp4' and not item['can_view_file']
        assert client.post('/api/v1/auth/logout', headers=test_single_admin_auth.csrf(client)).status_code == 204
        denied = client.post('/api/v1/search', json=body, headers=test_single_admin_auth.csrf(client))
        assert denied.status_code == 401 and 'phân số' not in denied.text
