-- REFERENCE ONLY. Never executed by existing migrations or API startup.
-- Derived from frozen revisions; do not use this file as an installer.
-- Historical files remain the replay authority. Comments are not copied.

-- Origin: alembic/versions/0007_timestamp_integrity.py
CREATE OR REPLACE FUNCTION master_set_local_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'Runtime created_at is immutable after insert' USING ERRCODE='23514';
    END IF;
    IF (to_jsonb(NEW) - ARRAY['created_at','updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['created_at','updated_at']) THEN
        NEW.updated_at := GREATEST(clock_timestamp(), OLD.updated_at + INTERVAL '1 microsecond', NEW.updated_at);
    ELSE
        NEW.updated_at := GREATEST(OLD.updated_at, NEW.updated_at);
    END IF;
    RETURN NEW;
END $$;

-- Origin: alembic/versions/0006_source_compatibility.py
CREATE FUNCTION master_guard_content_parent() RETURNS trigger LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
BEGIN
    IF NEW.parent_unit_id IS NOT NULL THEN
        IF current_setting('transaction_isolation') <> 'read committed' THEN
            RAISE EXCEPTION 'Content parent writes require READ COMMITTED; retry transaction'
                USING ERRCODE='25001';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended('content-parent:' || NEW.resource_version_id, 0));
        IF EXISTS (
            WITH RECURSIVE ancestors AS (
                SELECT id,parent_unit_id FROM content_units WHERE id=NEW.parent_unit_id
                UNION
                SELECT c.id,c.parent_unit_id FROM content_units c JOIN ancestors a ON c.id=a.parent_unit_id
            ) SELECT 1 FROM ancestors WHERE id=NEW.id
        ) THEN
            RAISE EXCEPTION 'Content unit parent cycle' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER trg_resource_versions_local_updated_at BEFORE UPDATE ON resource_versions FOR EACH ROW EXECUTE FUNCTION master_set_local_updated_at();
CREATE TRIGGER trg_ocr_jobs_local_updated_at BEFORE UPDATE ON ocr_jobs FOR EACH ROW EXECUTE FUNCTION master_set_local_updated_at();
CREATE TRIGGER trg_ocr_page_tasks_local_updated_at BEFORE UPDATE ON ocr_page_tasks FOR EACH ROW EXECUTE FUNCTION master_set_local_updated_at();
CREATE TRIGGER trg_content_units_local_updated_at BEFORE UPDATE ON content_units FOR EACH ROW EXECUTE FUNCTION master_set_local_updated_at();
CREATE TRIGGER trg_ocr_page_results_local_updated_at BEFORE UPDATE ON ocr_page_results FOR EACH ROW EXECUTE FUNCTION master_set_local_updated_at();
CREATE TRIGGER trg_content_parent_guard BEFORE INSERT OR UPDATE OF parent_unit_id,resource_version_id ON content_units FOR EACH ROW EXECUTE FUNCTION master_guard_content_parent();
