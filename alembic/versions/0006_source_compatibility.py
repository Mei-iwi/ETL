"""Preserve source nulls; constrain content parent scope; timestamp local-owned rows."""

import sqlalchemy as sa

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

NULLABLE = {
    "master_learning_resources": {
        "provider_id": sa.String(30),
        "provider_name": sa.String(255),
        "provider_type": sa.String(30),
        "provider_is_active": sa.Boolean(),
        "title": sa.String(500),
    },
    "master_users": {"full_name": sa.String(255)},
}
LOCAL_TABLES = (
    "resource_versions",
    "ocr_jobs",
    "ocr_page_tasks",
    "content_units",
    "ocr_page_results",
)


def upgrade():
    for table, columns in NULLABLE.items():
        with op.batch_alter_table(table) as batch:
            for column, kind in columns.items():
                batch.alter_column(column, existing_type=kind, nullable=True)
    if op.get_bind().dialect.name == "postgresql":
        op.create_unique_constraint(
            "uq_content_unit_version_id", "content_units", ["resource_version_id", "id"]
        )
        op.create_foreign_key(
            "fk_content_parent_same_version",
            "content_units",
            "content_units",
            ["resource_version_id", "parent_unit_id"],
            ["resource_version_id", "id"],
            ondelete="RESTRICT",
        )
        op.create_check_constraint(
            "ck_content_parent_not_self", "content_units", "parent_unit_id <> id"
        )
        op.execute("""
            CREATE FUNCTION master_set_local_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.updated_at IS NOT DISTINCT FROM OLD.updated_at AND
                   (to_jsonb(NEW) - ARRAY['created_at','updated_at']) IS DISTINCT FROM
                   (to_jsonb(OLD) - ARRAY['created_at','updated_at']) THEN
                    NEW.updated_at := GREATEST(clock_timestamp(), OLD.updated_at + INTERVAL '1 microsecond');
                END IF;
                RETURN NEW;
            END $$;
            COMMENT ON FUNCTION master_set_local_updated_at() IS
            'Cập nhật timestamp runtime nếu application chưa cập nhật; không áp dụng trên Master Data nguồn.';
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
            COMMENT ON FUNCTION master_guard_content_parent() IS
            'Chặn chu kỳ parent; chỉ duyệt tổ tiên khi parent khác NULL, khóa theo version tránh hai giao dịch tạo vòng đồng thời.';
            CREATE TRIGGER trg_content_parent_guard BEFORE INSERT OR UPDATE OF parent_unit_id,resource_version_id
            ON content_units FOR EACH ROW EXECUTE FUNCTION master_guard_content_parent();
            COMMENT ON TRIGGER trg_content_parent_guard ON content_units IS
            'Chặn vòng lặp parent; khóa theo version và kiểm tra tổ tiên trong READ COMMITTED.';
        """)
        for table in LOCAL_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_local_updated_at BEFORE UPDATE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION master_set_local_updated_at()"
            )
            op.execute(
                f"COMMENT ON TRIGGER trg_{table}_local_updated_at ON {table} IS 'Cập nhật timestamp runtime khi application chưa cập nhật; không áp dụng lên master_*.'"
            )


def downgrade():
    # Fail before any DDL if source NULL data would be lost. No fabricated replacements.
    for table, columns in NULLABLE.items():
        for column in columns:
            if op.get_bind().scalar(
                sa.text(f"SELECT count(*) FROM {table} WHERE {column} IS NULL")
            ):
                raise RuntimeError("Cannot downgrade nullable Master columns while NULL rows exist")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER trg_content_parent_guard ON content_units")
        op.execute("DROP FUNCTION master_guard_content_parent()")
        for table in LOCAL_TABLES:
            op.execute(f"DROP TRIGGER trg_{table}_local_updated_at ON {table}")
        op.execute("DROP FUNCTION master_set_local_updated_at()")
        op.drop_constraint("ck_content_parent_not_self", "content_units", type_="check")
        op.drop_constraint("fk_content_parent_same_version", "content_units", type_="foreignkey")
        op.drop_constraint("uq_content_unit_version_id", "content_units", type_="unique")
    for table, columns in NULLABLE.items():
        with op.batch_alter_table(table) as batch:
            for column, kind in columns.items():
                batch.alter_column(column, existing_type=kind, nullable=False)
