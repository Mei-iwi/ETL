"""Protect created_at and monotonic timestamps on ETL-owned PostgreSQL rows only."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
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
        COMMENT ON FUNCTION master_set_local_updated_at() IS
        '0007: giữ created_at và timestamp runtime không lùi; không áp dụng lên dữ liệu Master nguồn.';
    """)


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("""
        CREATE OR REPLACE FUNCTION master_set_local_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
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
    """)
