-- REVIEW-ONLY template.  Do not execute through ETL and do not run against
-- production until the SOURCE owner replaces every identifier and approves the
-- exact contract in deploy/config/source-write-contract.example.json.
-- No CREATE ROLE/DDL is included: the role must be provisioned by the DBA.
-- The approved dedicated login name for the optional staging writer is
-- etl_source_writer. Its password is deliberately not present in this file.
-- This database role is separate from the application actor allow-list in the
-- contract: the connection may authenticate successfully while UI/API writes
-- remain blocked until allowed_roles or allowed_actor_ids is explicitly set.

-- 1) Read-only probe for the dedicated writer role (run as that role):
SELECT current_user,
       has_table_privilege(current_user, 'learning_resources', 'SELECT') AS can_select,
       has_table_privilege(current_user, 'learning_resources', 'UPDATE') AS can_update,
       has_column_privilege(current_user, 'learning_resources', 'title', 'UPDATE') AS can_update_title;

-- 2) Least-privilege grants, only after SOURCE owner approves the real table
-- and column contract.  Never grant ALL, superuser, or public privileges.
-- GRANT CONNECT ON DATABASE <source_database> TO <etl_source_rw>;
-- GRANT USAGE ON SCHEMA <source_schema> TO <etl_source_rw>;
-- GRANT SELECT ON TABLE <source_schema>.learning_resources TO <etl_source_rw>;
-- Grant only the contract's whitelisted columns.  The current example uses
-- MASTER audit jobs for idempotency and therefore does not require a SOURCE
-- receipt column.  Add a receipt column only when the approved contract and
-- SOURCE schema already contain it; never create it from ETL.
-- GRANT UPDATE (title)
--   ON TABLE <source_schema>.learning_resources TO <etl_source_rw>;

-- 2a) Optional SOURCE grant pack supplied for etl_source_writer.
-- These statements remain commented because the current ETL contract only
-- performs an authorized title update.  A DBA may review/uncomment the exact
-- statements that match the approved two-way contract; granting rights here
-- does not enable CREATE/DELETE/version write-back in the application.
-- GRANT CONNECT ON DATABASE <source_database> TO etl_source_writer;
-- GRANT USAGE ON SCHEMA public TO etl_source_writer;
-- GRANT SELECT ON TABLE
--   public.learning_resources,
--   public.resource_versions,
--   public.resource_subjects,
--   public.resource_grade_levels,
--   public.providers,
--   public.resource_types,
--   public.subjects,
--   public.grade_levels
-- TO etl_source_writer;
-- GRANT SELECT (id, role_id, is_active)
--   ON TABLE public.users TO etl_source_writer;
-- GRANT INSERT (
--   id, provider_id, resource_type_id, created_by, title, publication_status,
--   is_active, created_at, updated_at
-- ) ON TABLE public.learning_resources TO etl_source_writer;
-- GRANT UPDATE (
--   title, publication_status, is_active, deleted_at, deleted_by, updated_at
-- ) ON TABLE public.learning_resources TO etl_source_writer;
-- GRANT INSERT (
--   id, resource_id, parent_version_id, version_number, lifecycle_status,
--   storage_bucket, storage_object_key, source_hash, uploaded_by,
--   created_at, updated_at
-- ) ON TABLE public.resource_versions TO etl_source_writer;
-- GRANT SELECT, INSERT, DELETE ON TABLE
--   public.resource_subjects, public.resource_grade_levels
-- TO etl_source_writer;
-- GRANT UPDATE (is_primary)
--   ON TABLE public.resource_subjects TO etl_source_writer;
-- CREATE also requires the existing SOURCE ID allocator.  The application
-- uses a transaction-bound INSERT ... ON CONFLICT UPDATE on id_counters;
-- these privileges are intentionally not included in the current update-only
-- contract and must be approved before enabling CREATE_RESOURCE.
-- GRANT INSERT, UPDATE ON TABLE public.id_counters TO etl_source_writer;

-- 2b) Verify the dedicated role after DBA provisioning (run as an owner or
-- SECURITY DEFINER probe; do not print the password or connection URL).
-- SELECT rolname, rolcanlogin, rolcreatedb, rolcreaterole, rolsuper
-- FROM pg_roles WHERE rolname = 'etl_source_writer';
-- SELECT
--   has_table_privilege('etl_source_writer', 'public.learning_resources', 'SELECT') AS can_read,
--   has_column_privilege('etl_source_writer', 'public.learning_resources', 'title', 'UPDATE') AS can_update,
--   has_column_privilege('etl_source_writer', 'public.learning_resources', 'id', 'INSERT') AS can_insert;

-- 3) Rollback (DBA applies only after stopping writeback):
-- REVOKE UPDATE (title)
--   ON TABLE <source_schema>.learning_resources FROM <etl_source_rw>;
-- REVOKE SELECT ON TABLE <source_schema>.learning_resources FROM <etl_source_rw>;
-- REVOKE USAGE ON SCHEMA <source_schema> FROM <etl_source_rw>;
-- REVOKE CONNECT ON DATABASE <source_database> FROM <etl_source_rw>;
