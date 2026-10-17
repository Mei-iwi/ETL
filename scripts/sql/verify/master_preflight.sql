-- MASTER ONLY, SELECT-only. Diagnostics, not permission to add constraints or edit rows.
-- Explicitly choose the Master schema, use a read-only transaction, then inspect counts.
SELECT current_database() AS database_name, current_schema() AS schema_name;
SELECT count(*) AS creator_orphans
FROM master_learning_resources r LEFT JOIN master_users u ON u.id = r.created_by
WHERE r.created_by IS NOT NULL AND u.id IS NULL;
SELECT count(*) AS subjects_without_grade
FROM subjects WHERE grade_id IS NULL;
SELECT count(*) AS subject_grade_orphans
FROM subjects s LEFT JOIN master_grade_levels g ON g.id = s.grade_id
WHERE s.grade_id IS NOT NULL AND g.id IS NULL;
SELECT count(*) AS versions_with_cross_resource_parent
FROM resource_versions v JOIN resource_versions p ON p.id = v.parent_version_id
WHERE v.resource_id IS DISTINCT FROM p.resource_id;
SELECT count(*) AS units_with_cross_version_parent
FROM content_units u JOIN content_units p ON p.id = u.parent_unit_id
WHERE u.resource_version_id IS DISTINCT FROM p.resource_version_id;
SELECT count(*) AS self_parent_versions FROM resource_versions WHERE id = parent_version_id;
SELECT count(*) AS self_parent_units FROM content_units WHERE id = parent_unit_id;
SELECT count(*) AS grades_code_longer_than_diagram
FROM master_grade_levels WHERE length(grade_code) > 30;
