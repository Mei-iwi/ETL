import hashlib
import json


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
