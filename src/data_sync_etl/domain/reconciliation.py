"""Pure reconciliation flags, comparisons and safe audit serialization."""
import json
from typing import Any

FLAG_SYNCED = "SYNCED"

FLAG_DESYNC_METADATA = "DESYNC_METADATA"

FLAG_DESYNC_BLOB_SIZE = "DESYNC_BLOB_SIZE"

FLAG_DESYNC_CHECKSUM = "DESYNC_CHECKSUM"

FLAG_ORPHAN_SOURCE = "ORPHAN_SOURCE"

FLAG_ORPHAN_MASTER = "ORPHAN_MASTER"

FLAG_MUTATED_SOURCE = "MUTATED_SOURCE"

FLAG_MUTATED_DOWNSTREAM = "MUTATED_DOWNSTREAM"

FLAG_NOT_VERIFIED = "NOT_VERIFIED"

METADATA_FIELDS = {
    'resources': ('title', 'publication_status', 'provider_id', 'resource_type_id', 'is_active', 'deleted_at'),
    'users': ('email', 'full_name', 'role_id', 'is_active', 'email_verified_at', 'deleted_at'),
    'subjects': ('subject_code', 'name', 'grade_level_id', 'is_active', 'deleted_at'),
    'grades': ('grade_code', 'grade_name_vi', 'education_level_id', 'is_active', 'deleted_at'),
}

def metadata_differences(stream, source, target):
    return [key for key in METADATA_FIELDS.get(stream, ()) if key in source and key in target
            and serialize_json_value(source[key]) != serialize_json_value(target[key])]

def serialize_json_value(value: Any) -> Any:
    """Đảm bảo mọi đối tượng datetime, UUID hoặc custom object đều được serialize an toàn trước khi lưu JSON."""
    if value is None:
        return None
    if isinstance(value, dict):
        secrets = {'password', 'hashed_password', 'password_hash', 'remember_token',
                   'access_token', 'refresh_token', 'session_token', 'api_key'}
        return {key: serialize_json_value(item) for key, item in value.items()
                if str(key).casefold() not in secrets}
    if isinstance(value, (list, tuple)):
        return [serialize_json_value(item) for item in value]
    return json.loads(json.dumps(value, default=str))

STREAM_TABLE_MAPPING: dict[str, dict[str, Any]] = {
    "resources": {
        "source_table": "learning_resources",
        "target_table": "master_learning_resources",
        "id_col": "id",
        "updated_col": "updated_at",
        "has_files": False,
    },
    "users": {
        "source_table": "users",
        "target_table": "master_users",
        "id_col": "id",
        "updated_col": "updated_at",
        "has_files": False,
    },
    "subjects": {
        "source_table": "subjects",
        "target_table": "subjects",
        "id_col": "id",
        "updated_col": "updated_at",
        "has_files": False,
    },
    "grades": {
        "source_table": "grade_levels",
        "target_table": "master_grade_levels",
        "id_col": "id",
        "updated_col": "updated_at",
        "has_files": False,
    },
    "files": {
        "source_table": "resource_versions",
        "target_table": "resource_versions",
        "id_col": "id",
        "updated_col": "updated_at",
        "has_files": True,
    },
}
