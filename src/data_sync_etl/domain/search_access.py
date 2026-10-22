"""Shared eligibility checks for public, verified local and authenticated access."""

from dataclasses import dataclass


def denied(record):
    if (record.get('deleted_at') is not None or record.get('source_missing_at') is not None
            or record.get('visibility', 'PUBLIC') != 'PUBLIC'
            or record.get('review_status', 'UNREVIEWED') not in ('UNREVIEWED', 'APPROVED', 'REVIEWED')
            or record.get('is_current', True) is not True or record.get('is_active', True) is not True):
        return True
    return any(key in record and record[key] is not False for key in (
        'acl_denied', 'license_restricted', 'access_denied', 'blocked', 'revoked', 'is_deleted', 'is_private'))


@dataclass(frozen=True)
class SearchAccess:
    scope: str = 'public'

    @property
    def internal(self):
        return self.scope in ('local_demo', 'authenticated_admin')

    # Authenticated scope is created by the API's server-side session boundary,
    # never from request JSON. Public rights remain unconfirmed.

    def allows(self, resource, version, unit):
        return self.evaluate(resource, version, unit) in ('ALLOW_LOCAL_DEMO', 'ALLOW_AUTHENTICATED_ADMIN')

    def evaluate(self, resource, version, unit):
        """Internal decision code, never serialized with private metadata."""
        # Callers must additionally verify CONTENT_READY, completed INDEX_BUILD,
        # current projection/generation and Bag fingerprint through IndexingService.
        if (resource.get('publication_status') != 'PUBLISHED' or resource.get('is_active') is not True
                or resource.get('deleted_at') is not None
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
        if not self.internal:
            return 'PUBLIC_RIGHTS_UNCONFIRMED'
        allowed = 'ALLOW_AUTHENTICATED_ADMIN' if self.scope == 'authenticated_admin' else 'ALLOW_LOCAL_DEMO'
        if review in ('APPROVED', 'REVIEWED'):
            return allowed if unit.get('retrieval_eligible') is True else 'RETRIEVAL_DENIED'
        # Only the explicit postprocess default can be read without review locally.
        # REJECTED, REVOKED, BLOCKED and all unknown statuses remain denied.
        return (allowed if review == 'UNREVIEWED' and unit.get('retrieval_eligible') is False
                else 'RETRIEVAL_DENIED')
