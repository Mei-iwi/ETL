CREATE FUNCTION source_set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['created_at','updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['created_at','updated_at']) THEN
        NEW.updated_at := GREATEST(clock_timestamp(), OLD.updated_at + INTERVAL '1 microsecond', NEW.updated_at);
    END IF;
    RETURN NEW;
END $$;
COMMENT ON FUNCTION source_set_updated_at() IS 'Trước UPDATE: đổi dữ liệu nghiệp vụ thì tăng timestamp; không thay created_at, không UPDATE đệ quy.';

CREATE FUNCTION source_require_soft_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Source metadata requires soft delete; hard delete cannot be reconciled by timestamp cursors'
        USING ERRCODE = '23514';
END $$;
COMMENT ON FUNCTION source_require_soft_delete() IS 'Chặn xóa cứng metadata để cursor không mất bản ghi; dùng is_active/deleted_at. Bảng nối vẫn được DELETE.';

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
COMMENT ON FUNCTION source_touch_resource() IS 'Sau thay đổi bảng nối: touch học liệu OLD/NEW, kể cả xóa liên kết cuối cùng; bảng nối không cần updated_at.';

CREATE FUNCTION source_touch_dependents() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (to_jsonb(NEW) - ARRAY['created_at','updated_at']) IS DISTINCT FROM
       (to_jsonb(OLD) - ARRAY['created_at','updated_at']) THEN
        EXECUTE format('UPDATE %I.%I SET updated_at = GREATEST(clock_timestamp(), updated_at + INTERVAL ''1 microsecond'') WHERE %I = $1',
            TG_TABLE_SCHEMA, TG_ARGV[0], TG_ARGV[1]) USING NEW.id;
    END IF;
    RETURN NULL;
END $$;
COMMENT ON FUNCTION source_touch_dependents() IS 'Sau UPDATE parent: đánh dấu child để ETL thấy metadata gộp mới; không cập nhật parent trở lại, không có vòng lặp.';
