"""Source change timestamps, dependency propagation and soft-delete contract."""

from pathlib import Path

from alembic import op

revision = "s0002"
down_revision = "s0001"
branch_labels = None
depends_on = None

TIMESTAMP_TABLES = (
    "education_levels",
    "grade_levels",
    "subjects",
    "providers",
    "resource_types",
    "roles",
    "users",
    "learning_resources",
    "resource_versions",
    "content_units",
    "ocr_jobs",
    "ocr_page_tasks",
)
METADATA_TABLES = TIMESTAMP_TABLES[:8]


def upgrade():
    op.execute((Path(__file__).parents[1] / "triggers_v1.sql").read_text(encoding="utf-8"))
    for table in TIMESTAMP_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION source_set_updated_at()"
        )
        op.execute(
            f"COMMENT ON TRIGGER trg_{table}_updated_at ON {table} IS 'Trước UPDATE: tăng timestamp khi dữ liệu nghiệp vụ đổi; giữ created_at.'"
        )
    for table in METADATA_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_soft_delete_only BEFORE DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION source_require_soft_delete()"
        )
        op.execute(
            f"COMMENT ON TRIGGER trg_{table}_soft_delete_only ON {table} IS 'Chặn xóa cứng metadata; dùng is_active/deleted_at để đồng bộ được.'"
        )
    for table in ("resource_subjects", "resource_grade_levels"):
        op.execute(
            f"CREATE TRIGGER trg_{table}_touch_resource AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION source_touch_resource()"
        )
        op.execute(
            f"COMMENT ON TRIGGER trg_{table}_touch_resource ON {table} IS 'Sau thêm/sửa/xóa liên kết: touch học liệu OLD/NEW trong cùng transaction.'"
        )
    for table, child, key in (
        ("providers", "learning_resources", "provider_id"),
        ("resource_types", "learning_resources", "resource_type_id"),
        ("roles", "users", "role_id"),
        ("education_levels", "grade_levels", "education_level_id"),
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_touch_dependents AFTER UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION source_touch_dependents('{child}', '{key}')"
        )
        op.execute(
            f"COMMENT ON TRIGGER trg_{table}_touch_dependents ON {table} IS 'Sau UPDATE parent: touch child tham chiếu để cập nhật metadata gộp; không có chiều ngược lại.'"
        )


def downgrade():
    for table in TIMESTAMP_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_updated_at ON {table}")
    for table in METADATA_TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_soft_delete_only ON {table}")
    for table in ("resource_subjects", "resource_grade_levels"):
        op.execute(f"DROP TRIGGER trg_{table}_touch_resource ON {table}")
    for table in ("providers", "resource_types", "roles", "education_levels"):
        op.execute(f"DROP TRIGGER trg_{table}_touch_dependents ON {table}")
    for function in (
        "source_set_updated_at",
        "source_require_soft_delete",
        "source_touch_resource",
        "source_touch_dependents",
    ):
        op.execute(f"DROP FUNCTION {function}()")
