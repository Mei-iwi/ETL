-- Read-only privilege probe.  Replace identifiers after SOURCE owner approval.
-- This file performs no GRANT/ALTER/CREATE/DELETE.
SELECT current_database() AS database_name,
       current_user AS role_name,
       pg_has_role(current_user, current_user, 'USAGE') AS role_is_usable,
       has_table_privilege(current_user, 'learning_resources', 'SELECT') AS can_select_resources,
       has_table_privilege(current_user, 'learning_resources', 'INSERT') AS can_insert_resources,
       has_table_privilege(current_user, 'learning_resources', 'UPDATE') AS can_update_resources,
       has_table_privilege(current_user, 'learning_resources', 'DELETE') AS can_delete_resources,
       has_column_privilege(current_user, 'learning_resources', 'title', 'UPDATE') AS can_update_title;

SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
FROM pg_roles
WHERE rolname = current_user;
