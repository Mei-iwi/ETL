"""Owner-only draft retrieval must never become shared public permission."""

import pytest

from data_sync_etl.domain.search_access import SearchAccess


@pytest.mark.parametrize('owner_id,grants,expected', [
    ('owner', frozenset({'resource'}), 'ALLOW_AUTHENTICATED_USER'),
    ('other', frozenset({'resource'}), 'RETRIEVAL_DENIED'),
    ('owner', frozenset(), 'RETRIEVAL_DENIED'),
    (None, frozenset({'resource'}), 'RETRIEVAL_DENIED'),
])
def test_owner_exception_does_not_promote_unreviewed_content_to_public(owner_id, grants, expected):
    resource = {'id': 'resource', 'created_by': 'owner', 'publication_status': 'PUBLISHED', 'is_active': True}
    version = {'id': 'version', 'resource_id': 'resource', 'lifecycle_status': 'APPROVED'}
    unit = {'resource_version_id': 'version', 'review_status': 'UNREVIEWED', 'retrieval_eligible': False}
    access = SearchAccess('authenticated_user', grants, owner_id=owner_id,
                          allow_public=True, allow_owned_unreviewed=True)
    assert access.evaluate(resource, version, unit) == expected
    unit['review_status'] = 'REJECTED'
    assert access.evaluate(resource, version, unit) == 'CONTENT_RESTRICTED'


@pytest.mark.parametrize('change', [
    {}, {'publication_status': 'DRAFT'}, {'is_active': False}, {'is_private': True},
    {'visibility': 'PRIVATE'}, {'source_missing_at': '2026-10-28'}, {'acl_denied': True},
])
def test_shared_capability_requires_published_unrestricted_resource(change):
    resource = {'id': 'resource', 'created_by': 'other', 'publication_status': 'PUBLISHED', 'is_active': True}
    version = {'id': 'version', 'resource_id': 'resource', 'lifecycle_status': 'INGESTED'}
    unit = {'resource_version_id': 'version', 'review_status': 'UNREVIEWED', 'retrieval_eligible': False}
    access = SearchAccess('authenticated_user', allow_public=True, allow_shared_unreviewed=True)
    assert access.allows({**resource, **change}, version, unit) is (not change)
    assert not SearchAccess(allow_public=True, allow_shared_unreviewed=True).allows(resource, version, unit)
    assert not SearchAccess('authenticated_user', allow_shared_unreviewed=True).allows(resource, version, unit)


@pytest.mark.parametrize('review,eligible', [('REJECTED', False), ('REVOKED', False),
    ('UNKNOWN', False), ('APPROVED', False), ('UNREVIEWED', True)])
def test_shared_capability_does_not_bypass_rejected_or_inconsistent_units(review, eligible):
    resource = {'id': 'resource', 'publication_status': 'PUBLISHED', 'is_active': True}
    version = {'id': 'version', 'resource_id': 'resource', 'lifecycle_status': 'INGESTED'}
    unit = {'resource_version_id': 'version', 'review_status': review, 'retrieval_eligible': eligible}
    assert not SearchAccess('authenticated_user', allow_public=True, allow_shared_unreviewed=True).allows(
        resource, version, unit)
