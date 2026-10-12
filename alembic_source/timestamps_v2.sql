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
COMMENT ON FUNCTION source_set_updated_at() IS
's0003: created_at bất biến sau INSERT; updated_at không lùi, tăng khi nghiệp vụ đổi, giữ timestamp lịch sử lúc INSERT.';
