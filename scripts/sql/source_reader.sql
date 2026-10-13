-- Execute as SOURCE owner once; set the password interactively with psql \password.
-- This role has no write grants; existing role/configuration requires operator review.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='etl_source_reader') THEN
        CREATE ROLE etl_source_reader LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
    ELSIF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='etl_source_reader'
                  AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
        RAISE EXCEPTION 'Existing source reader has elevated privileges; operator review required';
    END IF;
END $$;
GRANT CONNECT ON DATABASE etl_source TO etl_source_reader;
GRANT USAGE ON SCHEMA public TO etl_source_reader;
GRANT SELECT ON education_levels,grade_levels,subjects,providers,resource_types,roles,
    users,learning_resources,resource_subjects,resource_grade_levels,resource_versions TO etl_source_reader;
DO $$
DECLARE source_table text;
BEGIN
    FOREACH source_table IN ARRAY ARRAY['education_levels','grade_levels','subjects','providers',
        'resource_types','roles','users','learning_resources','resource_subjects','resource_grade_levels','resource_versions'] LOOP
        IF has_table_privilege('etl_source_reader', 'public.' || source_table, 'INSERT,UPDATE,DELETE,TRUNCATE') THEN
            RAISE EXCEPTION 'Existing source reader has write privileges; operator review required';
        END IF;
    END LOOP;
END $$;
-- \password etl_source_reader
