"""Real ASGI routes, isolated SQLite auth and explicit fake corpus authority.

Counters describe fixture rank reuse, not BGE throughput or live revocation.
"""
import pytest

from data_sync_etl.application.search_session_gateway import SearchSessionGateway
from data_sync_etl.application.search_sessions import RankedHit
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit import test_single_admin_auth as auth

credentials = auth.credentials
configured = auth.configured


@pytest.fixture
def session_api(configured):
    container, password, _ = configured
    # This adapter fixture never measures/loads a real embedding model.
    container.indexing.embedding.start_preload = lambda: None
    state = {'revision': 'offline-r1', 'rank': 0, 'hydrate': 0, 'complete': True, 'denied': False}
    refs = tuple(RankedHit(f'r{i}', f'v{i}', None, None, 1 / (i + 1)) for i in range(22))

    def rank(criteria, access):
        state['rank'] += 1
        return refs, not state['complete']

    def envelope(criteria, access):
        return dict(query=criteria['query'], method=criteria['method'], ranking_algorithm='fixture-only',
            representation_unit='content_bag', ready_bag_count=0, message='OK', access_scope=access.scope)

    def hydrate(selected, criteria, access):
        state['hydrate'] += 1
        return [] if state['denied'] else [dict(resource_id=hit.resource_id, resource_title='fresh title',
            resource_version_id=hit.version_id, version_number=1, provider_name=None, resource_type_code='mp3',
            bag_id=None, bag_title=None, unit_type=None, representation_mode='metadata', matched_keywords=[],
            matched_bag_count=0, score=hit.score, score_components={}, snippet='fresh metadata', page_from=None,
            page_to=None, content_unit_id=None, context_id=None, context_url=None, match_origin='METADATA',
            can_view_file=False) for hit in selected]

    gateway = SearchSessionGateway(lambda _: state['revision'], rank, hydrate, envelope)
    container.search_sessions = gateway
    container.settings.search_result_sessions_enabled = True
    def legacy(**criteria):
        return {**envelope(criteria, criteria['access']), 'items': [], 'page': criteria['page'],
                'size': criteria['size'], 'total': 0, 'total_pages': 0}
    container.search.search_http = legacy
    with auth.make_client(configured) as client:
        assert auth.login(client, password).status_code == 200
        yield client, container, state, gateway


def begin(client, **extra):
    return client.post('/api/v1/search', json=dict(query='raw English query', method='vector', size=5,
        window_pages=3, **extra), headers=auth.csrf(client))


def page(client, token, **extra):
    body = dict(query='raw English query', method='vector', size=5, page=4, session_id=token)
    body.update(extra)
    return client.post('/api/v1/search/page', json=body, headers=auth.csrf(client))


def test_http_rank_once_window_and_light_pages(session_api):
    client, _, state, gateway = session_api
    response = begin(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert response.headers['cache-control'] == 'private, no-store'
    assert len(result['items']) == 5 and [p['page'] for p in result['page_window']] == [2, 3]
    assert result['total_exact'] and result['total'] == 22
    for number in [4, 2, 1, 3]:
        value = page(client, result['search_session']['id'], page=number)
        assert value.status_code == 200, value.text
        assert value.json()['items'][0]['resource_id'] == f'r{(number - 1) * 5}'
    assert state['rank'] == 1 and state['hydrate'] == 7
    entry, = gateway.sessions._entries.values()
    assert not hasattr(entry, 'snippet') and not hasattr(entry, 'query')


@pytest.mark.parametrize('mutation', ['revision', 'denied', 'expired', 'query', 'scope', 'logout'])
def test_http_revocation_and_scope_never_leak_old_total(session_api, mutation):
    client, _, state, gateway = session_api
    token = begin(client).json()['search_session']['id']
    extra = {}
    if mutation == 'revision':
        state['revision'] = 'changed'
    if mutation == 'denied':
        state['denied'] = True
    if mutation == 'expired':
        gateway.sessions.clock = lambda: 10**20
    if mutation == 'query':
        extra['query'] = 'different'
    if mutation == 'scope':
        token = 'a' * 43
    if mutation == 'logout':
        assert client.post('/api/v1/auth/logout', headers=auth.csrf(client)).status_code == 204
    response = page(client, token, **extra)
    assert response.status_code == (401 if mutation == 'logout' else 409), response.text
    assert 'total' not in response.json() and 'fresh metadata' not in response.text
    assert state['rank'] == 1


@pytest.mark.parametrize('mode', ['disabled', 'missing_authority', 'truncated'])
def test_legacy_fallback_and_flag_off_parity(session_api, mode):
    client, container, state, _ = session_api
    if mode == 'disabled':
        container.settings.search_result_sessions_enabled = False
    if mode == 'missing_authority':
        state['revision'] = ''
    if mode == 'truncated':
        state['complete'] = False
    response = begin(client)
    assert response.status_code == 200, response.text
    result = response.json()
    assert 'search_session' not in result and 'page_window' not in result
    assert ('session_status' in result) == (mode != 'disabled')
    assert page(client, 'a' * 43).status_code == 409


def test_no_client_scope_and_window_bounds(session_api):
    client, _, _, gateway = session_api
    assert begin(client, actor_id='admin').status_code == 422
    assert client.post('/api/v1/search', json={'query': 'q', 'window_pages': 4},
        headers=auth.csrf(client)).status_code == 422
    with pytest.raises(SearchError, match='UNAVAILABLE'):
        gateway.keys({}, SearchAccess())


def test_viewer_page_read_does_not_require_admin_feature_flag(session_api, monkeypatch):
    from data_sync_etl.security.auth import Principal

    client, container, _, _ = session_api
    principal = Principal('master:fixture-viewer', 'VIEWER', 'master_users', 'offline-session')
    monkeypatch.setattr(client.app.state.auth, 'principal', lambda *args, **kwargs: principal)
    container.settings.admin_enabled = False
    response = begin(client)
    assert response.status_code == 200, response.text
    assert response.json()['access_scope'] == 'authenticated_user'
    response = page(client, response.json()['search_session']['id'])
    assert response.status_code == 200, response.text


def test_late_revision_change_is_blocked_at_http_send(session_api, monkeypatch):
    client, _, state, gateway = session_api
    token = begin(client).json()['search_session']['id']
    original = gateway.read
    def changed_after_hydration(*args, **kwargs):
        result = original(*args, **kwargs)
        state['revision'] = 'late-revocation'
        return result
    monkeypatch.setattr(gateway, 'read', changed_after_hydration)
    response = page(client, token)
    assert response.status_code == 409, response.text
    assert 'fresh metadata' not in response.text and 'total' not in response.json()


def test_cancelled_rank_cannot_publish_a_session():
    from data_sync_etl.application.search_runtime import CURRENT_SEARCH, SearchCall

    call = SearchCall(10, lambda: None, lambda: None)
    def rank(*args):
        call.cancel('fixture-disconnect')
        return [RankedHit('r', 'v', None, None, .8)], False
    gateway = SearchSessionGateway(lambda _: 'fixture-r1', rank, lambda *_: [], lambda *_: {'message': 'OK'})
    context = CURRENT_SEARCH.set(call)
    try:
        with pytest.raises(SearchError, match='SEARCH_TIMEOUT'):
            gateway.read({}, SearchAccess('authenticated_admin', actor_id='alice'), page=1, size=5)
        assert not gateway.sessions._entries
    finally:
        CURRENT_SEARCH.reset(context)
