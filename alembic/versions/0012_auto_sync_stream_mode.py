"""Persist the explicitly selected automatic sync mode per stream."""

import sqlalchemy as sa

from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sync_auto_streams", sa.Column("sync_mode", sa.String(20),
                                                nullable=False, server_default="INCREMENTAL"))


def downgrade():
    op.drop_column("sync_auto_streams", "sync_mode")
