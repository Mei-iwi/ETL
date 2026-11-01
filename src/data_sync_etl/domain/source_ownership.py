import hashlib
import json

from data_sync_etl.domain.core import DomainError

# SOURCE remains canonical for identity and relational fields.  Only fields in
# this explicit allow-list may ever cross the write-back boundary.
SOURCE_CANONICAL_FIELDS = frozenset({
    'title', 'provider_id', 'resource_type_id', 'publication_status',
})
MASTER_LOCAL_EXTENSION_FIELDS = frozenset({
    'description', 'tags', 'local_notes',
})
SOURCE_WRITE_BACK_FIELDS = frozenset({
    'title', 'provider_id', 'resource_type_id', 'publication_status',
})


def validate_source_writeback_fields(fields, *, allowed_fields=None):
    if not isinstance(fields, dict) or not fields:
        raise DomainError('SOURCE_WRITEBACK_FIELDS_REQUIRED')
    allowed = set(SOURCE_WRITE_BACK_FIELDS if allowed_fields is None else allowed_fields)
    unknown = set(fields) - allowed
    if unknown:
        raise DomainError('SOURCE_WRITEBACK_FIELD_BLOCKED')
    normalized = {}
    for field, value in fields.items():
        if field in {'title', 'provider_id', 'resource_type_id'}:
            if not isinstance(value, str) or not value.strip():
                raise DomainError(f'SOURCE_WRITEBACK_{field.upper()}_INVALID')
            limit = 500 if field == 'title' else 30
            if len(value.strip()) > limit:
                raise DomainError(f'SOURCE_WRITEBACK_{field.upper()}_INVALID')
            normalized[field] = value.strip()
        elif field == 'publication_status':
            if value not in {'DRAFT', 'PUBLISHED', 'UNPUBLISHED', 'ARCHIVED'}:
                raise DomainError('SOURCE_WRITEBACK_PUBLICATION_STATUS_INVALID')
            normalized[field] = value
        else:
            raise DomainError('SOURCE_WRITEBACK_FIELD_BLOCKED')
    return normalized


def state_hash(values):
    from data_sync_etl.domain.resource_deletion import LOCAL_DELETION_FIELDS
    # Identity is an immutable mapping, not mutable SOURCE state. Excluding it
    # keeps legacy ownership checks stable after the additive identity migration;
    # sync_upsert validates the mapping separately and fails closed on drift.
    excluded = {
        'source_origin', 'source_state_hash', 'source_missing_at',
        'source_identity_version', 'source_backend', 'source_namespace',
        'source_entity_type', 'source_record_key', 'source_identity_hash',
    } | LOCAL_DELETION_FIELDS
    return hashlib.sha256(json.dumps({k: v for k, v in values.items() if k not in excluded},
                                    sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
