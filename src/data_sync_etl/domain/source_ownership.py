import hashlib
import json


def state_hash(values):
    from data_sync_etl.domain.resource_deletion import LOCAL_DELETION_FIELDS
    excluded = {'source_origin', 'source_state_hash', 'source_missing_at'} | LOCAL_DELETION_FIELDS
    return hashlib.sha256(json.dumps({k: v for k, v in values.items() if k not in excluded},
                                    sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
