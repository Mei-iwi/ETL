"""Optional version processing preferences; preserve all existing content."""

import sqlalchemy as sa
from pathlib import Path
from runpy import run_path
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    media_columns = (
        ("resource_versions", "processing_options"),
        ("content_units", "source_locator"),
    )
    existing = [
        next((c for c in inspector.get_columns(table) if c["name"] == column), None)
        for table, column in media_columns
    ]
    if any(column is not None for column in existing):
        # A pre-merge media migration also used revision 0017. Accept only its
        # exact distinguishing layout; never stamp past an unknown partial schema.
        soft_delete = {"master_deleted_at", "master_deleted_by",
                       "master_deletion_reason", "deletion_revision"}
        resource_columns = {c["name"] for c in inspector.get_columns("master_learning_resources")}
        if (
            not all(c is not None and isinstance(c["type"], sa.JSON) and c["nullable"]
                    for c in existing)
            or soft_delete & resource_columns
            or "resource_deletion_events" in inspector.get_table_names()
        ):
            raise RuntimeError("0018_LEGACY_SCHEMA_REQUIRES_MANUAL_REVIEW")
        # Apply the missing soft-delete DDL in the same Alembic transaction.
        # Existing media columns and their values remain untouched.
        migration = run_path(str(Path(__file__).with_name("0017_resource_soft_delete.py")))
        migration["upgrade"]()
        return

    op.add_column(
        "resource_versions",
        sa.Column(
            "processing_options",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "content_units",
        sa.Column(
            "source_locator",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=True,
        ),
    )


def downgrade():
    connection = op.get_bind()
    for table, column in [
        ("resource_versions", "processing_options"),
        ("content_units", "source_locator"),
    ]:
        if connection.scalar(sa.text(f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL")):
            raise RuntimeError("0018 processing data exists; export it before downgrade")
    op.drop_column("content_units", "source_locator")
    op.drop_column("resource_versions", "processing_options")
