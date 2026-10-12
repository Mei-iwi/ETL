-- Run using the source migration owner, against the explicitly chosen SOURCE database.
SELECT current_database(), current_schema();
SELECT version_num FROM alembic_source_version;
SELECT count(*) AS business_tables FROM information_schema.tables
WHERE table_schema=current_schema() AND table_type='BASE TABLE' AND table_name <> 'alembic_source_version';
SELECT table_name,count(*) AS columns FROM information_schema.columns
WHERE table_schema=current_schema() AND table_name <> 'alembic_source_version'
GROUP BY table_name ORDER BY table_name;
SELECT table_name,column_name,data_type,udt_name,character_maximum_length,is_nullable,column_default
FROM information_schema.columns WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position;
SELECT t.typname,array_agg(e.enumlabel ORDER BY e.enumsortorder) AS values
FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid JOIN pg_namespace n ON n.oid=t.typnamespace
WHERE n.nspname=current_schema() GROUP BY t.typname ORDER BY t.typname;
SELECT r.relname,c.conname,c.contype,pg_get_constraintdef(c.oid)
FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
WHERE n.nspname=current_schema() ORDER BY r.relname,c.conname;
SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() ORDER BY tablename,indexname;
SELECT c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid),obj_description(t.oid,'pg_trigger') AS explanation
FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname=current_schema() AND NOT t.tgisinternal ORDER BY c.relname,t.tgname;
SELECT p.proname,p.prosecdef,p.proconfig,pg_get_functiondef(p.oid)
FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
WHERE n.nspname=current_schema() AND p.prorettype='trigger'::regtype ORDER BY p.proname;
SELECT prefix,period,last_seq FROM id_counters ORDER BY prefix,period;
