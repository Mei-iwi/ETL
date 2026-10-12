"""Independent source ERD: 15 business tables, 7 enums, 145 columns."""

from pathlib import Path

from alembic import op

revision = "s0001"
down_revision = None
branch_labels = None
depends_on = None

TABLES = (
    "id_counters",
    "education_levels",
    "grade_levels",
    "subjects",
    "providers",
    "resource_types",
    "roles",
    "users",
    "learning_resources",
    "resource_grade_levels",
    "resource_subjects",
    "resource_versions",
    "content_units",
    "ocr_jobs",
    "ocr_page_tasks",
)
ENUMS = (
    "provider_type",
    "publication_status",
    "lifecycle_status",
    "ocr_job_status",
    "ocr_page_task_status",
    "content_unit_type",
    "content_review_status",
)


def upgrade():
    op.execute((Path(__file__).parents[1] / "schema_v1.sql").read_text(encoding="utf-8"))


def downgrade():
    for table in reversed(TABLES):
        op.drop_table(table)
    for enum in reversed(ENUMS):
        op.execute(f"DROP TYPE {enum}")
