-- SELECT-only. Use a confirmed target and explicit search_path in a read-only transaction.
-- Run separately on SOURCE and Master; no public-schema assumption, no credentials/data rows.
SELECT current_database() AS database_name, current_schema() AS schema_name;
SELECT table_name, ordinal_position, column_name, data_type, udt_name,
       character_maximum_length, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = current_schema()
ORDER BY table_name, ordinal_position;
SELECT c.relname AS table_name, k.conname, k.contype, pg_get_constraintdef(k.oid) AS definition
FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = current_schema()
ORDER BY c.relname, k.conname;
SELECT tablename, indexname, indexdef
FROM pg_indexes WHERE schemaname = current_schema()
ORDER BY tablename, indexname;
SELECT c.relname AS table_name, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid) AS definition
FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = current_schema() AND NOT t.tgisinternal
ORDER BY c.relname, t.tgname;
SELECT p.proname, p.prosrc
FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = current_schema() AND p.prorettype = 'trigger'::regtype
ORDER BY p.proname;
