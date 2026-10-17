-- READ ONLY, NOT EXECUTED ON DEV. Run SOURCE and Master sections on separate
-- verified connections. Compare SOURCE parent IDs to Master import-map IDs
-- offline; no cross-database JOIN and no repair/backfill is authorized.
-- SOURCE connection:
SELECT id, resource_id, version_number, parent_version_id
FROM resource_versions ORDER BY resource_id, version_number, id;

-- MASTER connection (separate invocation):
SELECT i.source_system, i.source_version_id, i.resource_id,
       i.source_version_number, i.status, i.target_version_id,
       v.resource_id AS target_resource_id, v.version_number,
       v.parent_version_id, p.source_version_id AS mapped_parent_source_id,
       p.source_system AS mapped_parent_origin
FROM source_file_imports i
LEFT JOIN resource_versions v ON v.id = i.target_version_id
LEFT JOIN source_file_imports p ON p.target_version_id = v.parent_version_id
ORDER BY i.source_system, i.resource_id, i.source_version_number;
