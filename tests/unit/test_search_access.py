from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from data_sync_etl.api.search_access import verified_local_launch
from data_sync_etl.config import Settings
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


def local_client(c):
    return TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000))


def test_authenticated_reader_can_resolve_source_viewer_for_published_shared_resource():
    from types import SimpleNamespace

    from starlette.requests import Request

    from data_sync_etl.api.search_access import search_access
    from data_sync_etl.domain.metadata_search import metadata_allowed

    principal = SimpleNamespace(mode='master_users', role='USER', id='master:reader',
                                resource_ids=frozenset(), must_change_password=False)
    app = SimpleNamespace(state=SimpleNamespace(
        container=SimpleNamespace(settings=SimpleNamespace(auth_mode='master_users')),
        auth=SimpleNamespace(principal=lambda request: principal)))
    request = Request({'type': 'http', 'method': 'GET', 'path': '/api/v1/resources/shared/open',
                       'headers': [], 'query_string': b'', 'app': app})
    access = search_access(request)
    resource = {'id': 'shared', 'created_by': 'another-user', 'is_active': True,
                'publication_status': 'PUBLISHED', 'source_origin': 'POSTGRESQL'}
    version = {'id': 'v1', 'resource_id': 'shared', 'lifecycle_status': 'METADATA_ONLY'}
    assert access.internal and access.allow_public and access.allow_shared_unreviewed
    assert metadata_allowed(resource, version, access)


@pytest.mark.parametrize('searchable', [{'unreviewed': True}], indirect=True)
def test_demo_three_methods_no_credentials_allowlist_or_writes(searchable):
    c, version = searchable
    before = deepcopy(c.documents.units), deepcopy(c.indexing.bags.rows)
    with c.uow() as repo:
        runs = repo.find('etl_stage_runs', {})
    with local_client(c) as api:
        for method in ('bow', 'vector', 'hybrid'):
            body = api.post('/api/v1/search', json={'query': 'page', 'method': method}).json()
            assert body['total'] == 1 and body['access_scope'] == 'local_demo'
            url = body['items'][0]['context_url']
            response = api.get(url)
            assert response.status_code == 200 and response.json()['access_scope'] == 'local_demo'
        assert api.post('/api/v1/search', json={'query': 'page'}, auth=('unused', 'unused')).status_code == 200
        c.settings.search_internal_dev_enabled = False
        for method in ('bow', 'vector', 'hybrid'):
            body = api.post('/api/v1/search', json={'query': 'page', 'method': method}).json()
            assert body['total'] == body['ready_bag_count'] == 0 and body['items'] == []
            assert body['message'] == 'PUBLIC_ACCESS_UNCONFIRMED'
        assert api.get(url).status_code == 404
    assert before == (c.documents.units, c.indexing.bags.rows)
    with c.uow() as repo:
        assert repo.find('etl_stage_runs', {}) == runs
        assert all(row['review_status'] == 'UNREVIEWED' and row['retrieval_eligible'] is False
                   for row in repo.find('content_units', {'resource_version_id': version['id']}))


@pytest.mark.parametrize('failure', ['remote', 'production', 'staging', 'unset_env', 'no_admin',
                                   'proxy', 'forwarded', 'origin', 'host', 'unknown_launch', 'bind_all', 'proxy_launch'])
def test_untrusted_demo_requests_fail_closed(searchable, failure, monkeypatch):
    c, _ = searchable
    if failure in ('production', 'staging', 'unset_env'):
        c.settings.app_environment = None if failure == 'unset_env' else failure
    if failure == 'no_admin':
        c.settings.admin_enabled = False
    if failure in ('unknown_launch', 'bind_all', 'proxy_launch'):
        args = [] if failure == 'unknown_launch' else [
            'uvicorn/__main__.py', 'app', '--host', '0.0.0.0' if failure == 'bind_all' else '127.0.0.1',
            '--proxy-headers' if failure == 'proxy_launch' else '--no-proxy-headers']
        monkeypatch.setattr('sys.argv', args)
    headers = {'X-Internal': 'true', 'X-Role': 'admin', 'Origin': 'http://127.0.0.1'}
    if failure == 'proxy':
        headers['X-Forwarded-For'] = '127.0.0.1'
    if failure == 'forwarded':
        headers['Forwarded'] = 'for=127.0.0.1'
    if failure == 'origin':
        headers['Origin'] = 'https://attacker.example'
    with TestClient(create_app(c), base_url='http://attacker.example' if failure == 'host' else 'http://127.0.0.1',
                    client=('198.51.100.2' if failure == 'remote' else '127.0.0.1', 50000)) as api:
        for path, method, payload in [('/api/v1/search', 'post', {'query': 'page'}),
                                     ('/api/v1/chunks/valid-looking-token/context', 'get', None)]:
            response = api.request(method, path, json=payload, headers=headers)
            assert response.status_code == 403, response.text
            assert 'resource-1' not in response.text


@pytest.mark.parametrize('change', [
    {'review_status': 'REJECTED'}, {'review_status': 'UNKNOWN'}, {'acl_denied': True},
    {'license_restricted': None}, {'visibility': 'PRIVATE'}, {'revoked': True},
    {'is_active': False}, {'is_current': False}, {'source_missing_at': '2026-10-16'},
    {'review_status': 'APPROVED', 'retrieval_eligible': False},
    {'review_status': 'UNREVIEWED', 'retrieval_eligible': True},
])
def test_restrictions_never_bypassed(searchable, change):
    c, version = searchable
    with c.uow() as repo:
        resource = repo.get('master_learning_resources', version['resource_id'])
        unit = repo.find('content_units', {'resource_version_id': version['id']})[0]
    assert not SearchAccess('local_demo').allows(resource, version, {**unit, **change})
    assert not SearchAccess().allows(resource, version, unit)


@pytest.mark.parametrize('searchable', [{'structured': True, 'unreviewed': True, 'rejected_leaf': True}], indirect=True)
def test_denied_member_blocks_entire_bag(searchable):
    c, version = searchable
    assert c.indexing.status(version['id'])['index_status'] == 'READY'
    body = c.search.search('page', 'hybrid')
    assert body['items'] == [] and body['total'] == body['ready_bag_count'] == 0


@pytest.mark.parametrize('change', ['source_missing', 'rejected', 'not_content_ready'])
def test_direct_context_and_search_share_policy(searchable, change):
    from datetime import datetime, timezone

    c, version = searchable
    item = c.search.search('page', 'bow')['items'][0]
    with c.uow() as repo:
        if change == 'source_missing':
            repo.update('master_learning_resources', version['resource_id'], {'source_missing_at': datetime.now(timezone.utc)})
        elif change == 'rejected':
            for unit in repo.find('content_units', {'resource_version_id': version['id']}):
                repo.update('content_units', unit['id'], {'review_status': 'REJECTED'})
        else:
            run = repo.find('etl_stage_runs', {'resource_version_id': version['id'], 'stage': 'POSTPROCESS'})[0]
            repo.update('etl_stage_runs', run['id'], {'status': 'FAILED'})
    with local_client(c) as api:
        assert api.post('/api/v1/search', json={'query': 'page'}).json()['total'] == 0
        response = api.get(item['context_url'])
        assert response.status_code == 404 and item['snippet'] not in response.text


def test_client_cannot_select_scope_and_public_does_not_load_model(searchable):
    c, _ = searchable
    c.settings.search_internal_dev_enabled = False
    c.indexing.embedding.prepare = lambda: pytest.fail('public must not access private corpus/model')
    with local_client(c) as api:
        for flag in ('internal', 'allow_public', 'allow_shared_unreviewed'):
            assert api.post('/api/v1/search', json={'query': 'page', flag: True}).status_code == 422
        assert api.post('/api/v1/search', json={'query': 'page'}).json()['items'] == []


def test_default_off_and_production_refuses_demo():
    assert not Settings(_env_file=None).search_internal_dev_enabled
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_environment='production', search_internal_dev_enabled=True)


@pytest.mark.parametrize('host', ['127.0.0.1', '::1'])
def test_numeric_loopback_launch_only(monkeypatch, host):
    monkeypatch.setattr('data_sync_etl.api.search_access.container_runtime', lambda: False)
    monkeypatch.setattr('sys.argv', ['uvicorn/__main__.py', 'app', '--host', host, '--no-proxy-headers'])
    assert verified_local_launch()


@pytest.mark.parametrize('extra', [['--host=0.0.0.0'], ['--fd', '3'], ['--uds=/tmp/socket']])
def test_ambiguous_or_inherited_bind_rejected(monkeypatch, extra):
    monkeypatch.setattr('data_sync_etl.api.search_access.container_runtime', lambda: False)
    monkeypatch.setattr('sys.argv', ['uvicorn/__main__.py', 'app', '--host', '127.0.0.1', '--no-proxy-headers', *extra])
    assert not verified_local_launch()


def test_container_demo_rejected(monkeypatch):
    monkeypatch.setattr('data_sync_etl.api.search_access.container_runtime', lambda: True)
    monkeypatch.setattr('sys.argv', ['uvicorn/__main__.py', 'app', '--host', '127.0.0.1', '--no-proxy-headers'])
    assert not verified_local_launch()


@pytest.mark.parametrize('searchable', [{'structured': True, 'restricted_parent': True}], indirect=True)
def test_ancestor_retrieval_denial_blocks_bag_and_context(searchable):
    from data_sync_etl.domain.search_context import encode_context

    c, version = searchable
    assert c.indexing.status(version['id'])['index_status'] == 'READY'
    with c.uow() as repo:
        snapshot = c.indexing._snapshot(repo, version['id'])
    leaf = next(unit for unit in snapshot[2] if unit['parent_unit_id'] is not None and unit['retrieval_eligible'])
    token = encode_context(version['id'], leaf['id'], 1, snapshot[3])
    with local_client(c) as api:
        assert api.post('/api/v1/search', json={'query': 'page'}).json()['items'] == []
        assert api.get(f'/api/v1/chunks/{token}/context').status_code == 404
