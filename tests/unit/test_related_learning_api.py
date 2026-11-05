from types import SimpleNamespace
from unittest.mock import Mock

from data_sync_etl.adapters.persistence.graph_mapping import PostgresGraphMapping
from data_sync_etl.adapters.persistence.related_learning import RelatedLearningAuthority
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_master_auth import configured as configured
from tests.unit.test_master_auth import credentials as credentials
from tests.unit.test_master_auth import login
from tests.unit.test_master_auth import master_auth as master_auth
from tests.unit.test_search import searchable as searchable
from tests.unit.test_single_admin_auth import make_client


def test_official_master_login_required_and_empty_shadow_does_not_read_graph(master_auth):
    container = master_auth[0]
    container.related_learning = SimpleNamespace(run=Mock(return_value=[]), close=Mock())
    with make_client(master_auth) as api:
        assert api.get('/api/v1/resources/resource-1/related-learning').status_code == 401
        container.related_learning.run.assert_not_called()
        assert login(api, master_auth).status_code == 200
        response = api.get('/api/v1/resources/resource-1/related-learning')
        assert response.status_code == 200 and response.json() == {'items': []}
        assert response.headers['cache-control'] == 'private, no-store'
        access = container.related_learning.run.call_args.args[1]
        assert access.scope == 'authenticated_user' and access.owner_id == 'user-1'
        assert access.allow_owned_unreviewed is True


def test_authority_excludes_private_draft_for_stranger_and_revalidates_owner(searchable):
    container, version = searchable
    authority = RelatedLearningAuthority(container.uow, container.search,
        PostgresGraphMapping(container.engine), container.settings)
    admin = SearchAccess('authenticated_admin', actor_id='master:admin')
    viewer = SearchAccess('authenticated_user', actor_id='master:stranger',
        owner_id='stranger', allow_public=True, allow_shared_unreviewed=True)
    owner = SearchAccess('authenticated_user', frozenset({'resource-1'}),
        actor_id='master:user-1', owner_id='user-1', allow_public=True,
        allow_shared_unreviewed=True, allow_owned_unreviewed=True)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1',
                    {'publication_status': 'DRAFT', 'created_by': 'user-1'})
    assert 'resource-1' in authority.read('resource-1', admin)
    assert authority.read('resource-1', viewer) == {}
    assert 'resource-1' in authority.read('resource-1', owner)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {'created_by': 'user-2'})
    assert authority.read('resource-1', owner) == {}


def test_graph_authority_never_loads_contentbag_or_content_search(searchable, monkeypatch):
    container, version = searchable
    def forbidden(*_args, **_kwargs):
        raise AssertionError('related API must not load the Search corpus')
    monkeypatch.setattr(container.indexing, '_snapshot', forbidden)
    monkeypatch.setattr(container.search, '_content_search', forbidden)
    monkeypatch.setattr(container.indexing.bags, 'search_candidates', forbidden)
    authority = RelatedLearningAuthority(container.uow, container.search,
        PostgresGraphMapping(container.engine), container.settings)
    assert version['resource_id'] in authority.read(version['resource_id'],
                                                  SearchAccess('authenticated_admin'))


def test_changed_identity_after_graph_io_is_forbidden_not_empty_success(master_auth, monkeypatch):
    container = master_auth[0]
    container.related_learning = SimpleNamespace(run=Mock(return_value=[{'title': 'protected'}]), close=Mock())
    with make_client(master_auth) as api:
        assert login(api, master_auth).status_code == 200
        monkeypatch.setattr('data_sync_etl.api.related_learning_routes.search_access', Mock(side_effect=[
            SearchAccess('authenticated_user', actor_id='master:first'),
            SearchAccess('authenticated_user', actor_id='master:changed')]))
        response = api.get('/api/v1/resources/resource-1/related-learning')
        assert response.status_code == 403
        assert response.json() == {'detail': {'code': 'AUTHORIZATION_CHANGED'}}
