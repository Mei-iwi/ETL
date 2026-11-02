from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.metadata_search import metadata_allowed
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.security.auth import AuthError
from data_sync_etl.security.authorization import authorize


def service(resource, version):
    class Repository:
        def get(self, table, _id):
            return version if table == 'resource_versions' else resource

    @contextmanager
    def uow():
        yield Repository()

    instance = SearchService.__new__(SearchService)
    instance.indexing = SimpleNamespace(uow=uow)
    return instance


def records(publication='DRAFT'):
    return ({'id': 'r1', 'source_origin': 'POSTGRESQL', 'is_active': True,
             'publication_status': publication, 'created_by': 'owner'},
            {'id': 'v1', 'resource_id': 'r1', 'lifecycle_status': 'METADATA_ONLY'})


@pytest.mark.parametrize('publication', ['DRAFT', 'PUBLISHED'])
def test_admin_and_granted_owner_can_process_without_publishing(publication):
    resource, version = records(publication)
    for access in (SearchAccess(scope='authenticated_admin', actor_id='admin'),
                   SearchAccess(scope='authenticated_user', actor_id='operator', owner_id='owner',
                                resource_ids=frozenset({'r1'}))):
        service(resource, version).authorize_source_processing_version('v1', access=access)
    assert resource['publication_status'] == publication
    if publication == 'DRAFT':
        assert not metadata_allowed(resource, version, SearchAccess(scope='authenticated_admin', actor_id='admin'))


@pytest.mark.parametrize('access', [
    SearchAccess(),
    SearchAccess(scope='authenticated_user', actor_id='operator', owner_id='owner'),
    SearchAccess(scope='authenticated_user', actor_id='operator', owner_id='other', resource_ids=frozenset({'r1'})),
])
def test_ungranted_user_is_denied(access):
    resource, version = records()
    with pytest.raises(SearchError, match='FILE_NOT_FOUND'):
        service(resource, version).authorize_source_processing_version('v1', access=access)


@pytest.mark.parametrize(('record', 'field', 'value'), [
    ('resource', 'is_active', False), ('resource', 'source_missing_at', 'revoked'),
    ('resource', 'deleted_at', 'deleted'), ('version', 'lifecycle_status', 'REJECTED'),
    ('version', 'resource_id', 'other'), ('version', 'rejection_reason', 'rejected'),
])
def test_revoked_deleted_and_invalid_versions_are_denied(record, field, value):
    resource, version = records()
    (resource if record == 'resource' else version)[field] = value
    with pytest.raises(SearchError, match='FILE_NOT_FOUND'):
        service(resource, version).authorize_source_processing_version(
            'v1', access=SearchAccess(scope='authenticated_admin', actor_id='admin'))


@pytest.mark.parametrize(('role', 'permissions', 'allowed'), [
    ('ADMIN', set(), True), ('OPERATOR', {'process'}, True), ('OPERATOR', set(), False), ('USER', {'process'}, False),
])
def test_http_processing_role_is_enforced_before_domain_access(role, permissions, allowed):
    resource, version = records()
    app = SimpleNamespace(state=SimpleNamespace(container=SimpleNamespace(indexing=service(resource, version).indexing)))
    app.state.container.uow = app.state.container.indexing.uow
    request = SimpleNamespace(url=SimpleNamespace(path='/api/v1/resource-versions/v1/process'), method='POST', app=app)
    principal = SimpleNamespace(must_change_password=False, role=role, mode='master_users',
                                id='master:owner', resource_ids={'r1'}, permissions=permissions)
    if allowed:
        authorize(request, principal)
    else:
        with pytest.raises(AuthError):
            authorize(request, principal)
