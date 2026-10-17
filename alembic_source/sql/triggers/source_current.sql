-- REFERENCE ONLY. Never executed by existing migrations or API startup.
-- Derived from frozen revisions; do not use this file as an installer.
-- Historical files remain the replay authority. Comments are not copied.

-- Origin: alembic_source/timestamps_v2.sql
CREATE OR REPLACE FUNCTION source_set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'Source created_at is immutable after insert' USING ERRCODE='23514';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['created_at','updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['created_at','updated_at']) THEN
        NEW.updated_at := GREATEST(clock_timestamp(), OLD.updated_at + INTERVAL '1 microsecond', NEW.updated_at);
    ELSE
        NEW.updated_at := GREATEST(OLD.updated_at, NEW.updated_at);
    END IF;
    RETURN NEW;
END $$;

-- Origin: alembic_source/triggers_v1.sql
CREATE FUNCTION source_require_soft_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Source metadata requires soft delete; hard delete cannot be reconciled by timestamp cursors'
        USING ERRCODE = '23514';
END $$;

-- Origin: alembic_source/triggers_v1.sql
CREATE FUNCTION source_touch_resource() RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF TG_OP <> 'INSERT' THEN
        UPDATE learning_resources SET updated_at = GREATEST(clock_timestamp(), updated_at + INTERVAL '1 microsecond')
        WHERE id = OLD.resource_id;
    END IF;
    IF TG_OP = 'INSERT' OR (TG_OP = 'UPDATE' AND NEW.resource_id IS DISTINCT FROM OLD.resource_id) THEN
        UPDATE learning_resources SET updated_at = GREATEST(clock_timestamp(), updated_at + INTERVAL '1 microsecond')
        WHERE id = NEW.resource_id;
    END IF;
    RETURN NULL;
END $$;

-- Origin: alembic_source/triggers_v1.sql
CREATE FUNCTION source_touch_dependents() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['created_at','updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['created_at','updated_at']) THEN
        EXECUTE format('UPDATE %I.%I SET updated_at = GREATEST(clock_timestamp(), updated_at + INTERVAL ''1 microsecond'') WHERE %I = $1',
            TG_TABLE_SCHEMA, TG_ARGV[0], TG_ARGV[1]) USING NEW.id;
    END IF;
    RETURN NULL;
END $$;

CREATE TRIGGER trg_education_levels_updated_at BEFORE UPDATE ON education_levels FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_grade_levels_updated_at BEFORE UPDATE ON grade_levels FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_subjects_updated_at BEFORE UPDATE ON subjects FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_providers_updated_at BEFORE UPDATE ON providers FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_resource_types_updated_at BEFORE UPDATE ON resource_types FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_roles_updated_at BEFORE UPDATE ON roles FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_learning_resources_updated_at BEFORE UPDATE ON learning_resources FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_resource_versions_updated_at BEFORE UPDATE ON resource_versions FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_content_units_updated_at BEFORE UPDATE ON content_units FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_ocr_jobs_updated_at BEFORE UPDATE ON ocr_jobs FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_ocr_page_tasks_updated_at BEFORE UPDATE ON ocr_page_tasks FOR EACH ROW EXECUTE FUNCTION source_set_updated_at();
CREATE TRIGGER trg_education_levels_soft_delete_only BEFORE DELETE ON education_levels FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_grade_levels_soft_delete_only BEFORE DELETE ON grade_levels FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_subjects_soft_delete_only BEFORE DELETE ON subjects FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_providers_soft_delete_only BEFORE DELETE ON providers FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_resource_types_soft_delete_only BEFORE DELETE ON resource_types FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_roles_soft_delete_only BEFORE DELETE ON roles FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_users_soft_delete_only BEFORE DELETE ON users FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_learning_resources_soft_delete_only BEFORE DELETE ON learning_resources FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete();
CREATE TRIGGER trg_resource_subjects_touch_resource AFTER INSERT OR UPDATE OR DELETE ON resource_subjects FOR EACH ROW EXECUTE FUNCTION source_touch_resource();
CREATE TRIGGER trg_resource_grade_levels_touch_resource AFTER INSERT OR UPDATE OR DELETE ON resource_grade_levels FOR EACH ROW EXECUTE FUNCTION source_touch_resource();
CREATE TRIGGER trg_providers_touch_dependents AFTER UPDATE ON providers FOR EACH ROW EXECUTE FUNCTION source_touch_dependents('learning_resources', 'provider_id');
CREATE TRIGGER trg_resource_types_touch_dependents AFTER UPDATE ON resource_types FOR EACH ROW EXECUTE FUNCTION source_touch_dependents('learning_resources', 'resource_type_id');
CREATE TRIGGER trg_roles_touch_dependents AFTER UPDATE ON roles FOR EACH ROW EXECUTE FUNCTION source_touch_dependents('users', 'role_id');
CREATE TRIGGER trg_education_levels_touch_dependents AFTER UPDATE ON education_levels FOR EACH ROW EXECUTE FUNCTION source_touch_dependents('grade_levels', 'education_level_id');
