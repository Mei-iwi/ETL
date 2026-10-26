"""Shared eligibility checks for public, verified local and authenticated access."""

from dataclasses import dataclass

from data_sync_etl.domain.resource_deletion import resource_deleted


def denied(record):
    if (resource_deleted(record) or record.get('source_missing_at') is not None
            or record.get('visibility', 'PUBLIC') != 'PUBLIC'
            or record.get('review_status', 'UNREVIEWED') not in ('UNREVIEWED', 'APPROVED', 'REVIEWED')
            or record.get('is_current', True) is not True or record.get('is_active', True) is not True):
        return True
    return any(key in record and record[key] is not False for key in (
        'acl_denied', 'license_restricted', 'access_denied', 'blocked', 'revoked', 'is_deleted', 'is_private'))


@dataclass(frozen=True)
class SearchAccess:
    scope: str = 'public'
    resource_ids: frozenset[str] = frozenset()
    actor_id: str | None = None
    owner_id: str | None = None
    allow_public: bool = False
    allow_owned_unreviewed: bool = False

    @property
    def internal(self):
        return self.scope in ('local_demo', 'authenticated_admin', 'authenticated_user')

    # Authenticated scope is created by the API's server-side session boundary,
    # never from request JSON. Public whole-file reads additionally require every
    # unit through file_version. Only the owner can search/preview their unreviewed
    # file; this does not publish it or expose it to other users' shared search.

    def allows(self, resource, version, unit):
        return self.evaluate(resource, version, unit) in ('ALLOW_LOCAL_DEMO', 'ALLOW_AUTHENTICATED_ADMIN', 'ALLOW_AUTHENTICATED_USER')

    def evaluate(self, resource, version, unit):
        """Internal decision code, never serialized with private metadata."""
        # Callers must additionally verify CONTENT_READY, completed INDEX_BUILD,
        # current projection/generation and Bag fingerprint through IndexingService.
        if (resource.get('publication_status') != 'PUBLISHED' or resource.get('is_active') is not True
                or resource_deleted(resource)
                or version.get('resource_id') != resource.get('id')
                or version.get('lifecycle_status') not in ('INGESTED', 'APPROVED', 'PUBLISHED')
                or unit.get('resource_version_id') != version.get('id')):
            return 'RESOURCE_NOT_ACCESSIBLE'
        # No ACL/license columns exist in the current PG contracts. Do not ignore
        # explicit restrictions if supplied by a trusted authorization integration.
        for record in (resource, version, unit):
            if denied(record):
                return 'CONTENT_RESTRICTED'
        review = unit.get('review_status')
        if self.scope == 'authenticated_user':
            owned = (self.owner_id is not None and resource.get('created_by') == self.owner_id
                     and resource['id'] in self.resource_ids)
            if (self.allow_owned_unreviewed and owned and review == 'UNREVIEWED'
                    and unit.get('retrieval_eligible') is False):
                return 'ALLOW_AUTHENTICATED_USER'
            if not self.allow_public and resource['id'] not in self.resource_ids:
                return 'RESOURCE_NOT_GRANTED'
            if not self.allow_public and self.owner_id is not None and resource.get('created_by') != self.owner_id:
                return 'RESOURCE_NOT_OWNED'
            return ('ALLOW_AUTHENTICATED_USER' if review in ('APPROVED', 'REVIEWED')
                    and unit.get('retrieval_eligible') is True else 'RETRIEVAL_DENIED')
        if not self.internal:
            return 'PUBLIC_RIGHTS_UNCONFIRMED'
        allowed = 'ALLOW_AUTHENTICATED_ADMIN' if self.scope == 'authenticated_admin' else 'ALLOW_LOCAL_DEMO'
        if review in ('APPROVED', 'REVIEWED'):
            return allowed if unit.get('retrieval_eligible') is True else 'RETRIEVAL_DENIED'
        # Only the explicit postprocess default can be read without review locally.
        # REJECTED, REVOKED, BLOCKED and all unknown statuses remain denied.
        return (allowed if review == 'UNREVIEWED' and unit.get('retrieval_eligible') is False
                else 'RETRIEVAL_DENIED')
