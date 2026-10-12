"""Validate numeric source invariants and protect source timestamps."""

from pathlib import Path

from alembic import op

revision = "s0003"
down_revision = "s0002"
branch_labels = None
depends_on = None

CHECKS = (
    ("resource_versions", "ck_source_version_positive", "version_number >= 1"),
    ("content_units", "ck_source_sequence_positive", "sequence_no >= 1"),
    (
        "content_units",
        "ck_source_page_range",
        "(page_from IS NULL OR page_from >= 1) AND (page_to IS NULL OR page_to >= 1) AND (page_from IS NULL OR page_to IS NULL OR page_from <= page_to)",
    ),
    ("ocr_page_tasks", "ck_source_page_positive", "page_num >= 1"),
    ("ocr_page_tasks", "ck_source_retry_nonnegative", "retry_count >= 0"),
    (
        "ocr_jobs",
        "ck_source_page_counts",
        "total_pages >= 0 AND completed_pages >= 0 AND failed_pages >= 0 AND completed_pages + failed_pages <= total_pages",
    ),
)


def upgrade():
    # Preflight all checks before DDL; reject bad history instead of repairing it silently.
    for table, name, expression in CHECKS:
        if (
            op.get_bind()
            .exec_driver_sql(f"SELECT EXISTS (SELECT 1 FROM {table} WHERE NOT ({expression}))")
            .scalar()
        ):
            raise RuntimeError(
                f"SOURCE preflight failed: {name}; review existing rows before migration"
            )
    for table, name, expression in CHECKS:
        op.create_check_constraint(name, table, expression)
    op.execute((Path(__file__).parents[1] / "timestamps_v2.sql").read_text(encoding="utf-8"))


def downgrade():
    # Restore only the timestamp function, not the unrelated functions/trigger ownership.
    original = (Path(__file__).parents[1] / "triggers_v1.sql").read_text(encoding="utf-8")
    original = original.split("CREATE FUNCTION source_require_soft_delete")[0]
    op.execute(original.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    for table, name, _expression in reversed(CHECKS):
        op.drop_constraint(name, table, type_="check")
