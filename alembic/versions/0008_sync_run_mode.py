"""Record the requested mode for each Master Data sync run."""

import sqlalchemy as sa

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("sync_runs", sa.Column("sync_mode", sa.String(20), nullable=True))


def downgrade():
    op.drop_column("sync_runs", "sync_mode")
