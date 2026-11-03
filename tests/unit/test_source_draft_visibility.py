from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.persistence.search_reader import SearchRepositoryMixin
from data_sync_etl.application.source_file_resolver import SourceFileResolver
from data_sync_etl.domain.metadata_search import metadata_allowed
from data_sync_etl.domain.search_access import SearchAccess


@pytest.mark.parametrize(('access', 'allowed'), [
    (SearchAccess(), False),
    (SearchAccess('local_demo'), False),
    (SearchAccess('authenticated_admin'), False),
    (SearchAccess('authenticated_admin', actor_id='master:admin'), True),
    (SearchAccess('authenticated_user', actor_id='master:other', allow_public=True,
                  allow_shared_unreviewed=True), False),
    (SearchAccess('authenticated_user', frozenset({'r1'}), 'master:owner', 'owner',
                  allow_owned_unreviewed=True), True),
    (SearchAccess('authenticated_user', frozenset({'r1'}), 'master:other', 'other',
                  allow_owned_unreviewed=True), False),
])
def test_draft_visibility_requires_live_authenticated_view_grant(access, allowed):
    resource = dict(id='r1', created_by='owner', publication_status='DRAFT', is_active=True)
    version = dict(id='v1', resource_id='r1', lifecycle_status='INGESTED')
    unit = dict(id='u1', resource_version_id='v1', review_status='UNREVIEWED', retrieval_eligible=False)
    assert access.allows(resource, version, unit) is allowed
    assert metadata_allowed(resource, version, access) is allowed
    resource['is_active'] = False
    assert not access.allows(resource, version, unit)
    assert not metadata_allowed(resource, version, access)


def test_search_candidate_query_receives_draft_grant_without_opening_database():
    repo = SearchRepositoryMixin()
    repo.session = MagicMock()
    access = SearchAccess('authenticated_admin', actor_id='master:admin')
    repo.search_versions({}, limit=5, metadata_access=access)
    statement = repo.session.execute.call_args.args[0]
    values = statement.compile().params.values()
    assert any(isinstance(value, (list, tuple)) and 'DRAFT' in value for value in values)


def test_shared_published_permission_never_expands_to_draft():
    access = SearchAccess('authenticated_user', actor_id='master:reader',
                          allow_public=True, allow_shared_unreviewed=True)
    resource = dict(id='r1', publication_status='PUBLISHED', is_active=True)
    version = dict(id='v1', resource_id='r1', lifecycle_status='INGESTED')
    unit = dict(resource_version_id='v1', review_status='UNREVIEWED', retrieval_eligible=False)
    assert access.allows(resource, version, unit)
    resource['publication_status'] = 'DRAFT'
    assert not access.allows(resource, version, unit)


def test_source_revocation_and_hash_change_are_checked_again_without_metadata_sync():
    resource = dict(id='r1', source_record_key='sr1', source_origin='POSTGRESQL',
                    publication_status='DRAFT', is_active=True)
    version = dict(id='v1', resource_id='r1', version_number=1, source_hash='a' * 64,
                   lifecycle_status='INGESTED')
    imported = dict(source_system='source-a', source_version_id='sv1', resource_id='r1',
                    source_hash='a' * 64, status='METADATA_ONLY')
    source = dict(resource_id='sr1', source_version_id='sv1', source_version_number=1,
                  source_hash='a' * 64, resource_active=True, resource_publication_status='DRAFT',
                  source_lifecycle_status='APPROVED')
    adapter = SimpleNamespace(get_versions=MagicMock(side_effect=lambda _: {'sv1': dict(source)}))
    repo = SimpleNamespace(source_index_authorities=lambda _: {'v1': (version, resource, [imported])})

    @contextmanager
    def uow():
        yield repo

    resolver = SourceFileResolver.__new__(SourceFileResolver)
    resolver.uow = uow
    resolver.settings = SimpleNamespace(search_max_bags=10, source_namespace='source-a',
                                        source_file_allowed_statuses=('APPROVED',))
    resolver._source_adapter = lambda _: adapter
    resolver.require_content_capabilities = MagicMock()
    access = SearchAccess('authenticated_admin', actor_id='master:admin')
    assert resolver.derived_index_authorized('v1', access=access)
    source['resource_active'] = False
    assert not resolver.derived_index_authorized('v1', access=access)
    source['resource_active'] = True
    source['source_hash'] = 'b' * 64
    assert not resolver.derived_index_authorized('v1', access=access)
    assert adapter.get_versions.call_count == 3
    assert resource['is_active'] and version['source_hash'] == 'a' * 64
    source['source_hash'] = 'a' * 64
    resource['source_namespace'] = 'other-tenant'
    assert not resolver.derived_index_authorized('v1', access=access)
