"""Optional version processing preferences; preserve all existing content."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
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
