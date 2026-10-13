"""Persist Auto Incremental enablement for each Master Data stream."""

from datetime import UTC, datetime

import sqlalchemy as sa

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

STREAMS = ("resources", "users", "grades", "subjects")


def upgrade():
    table = op.create_table(
        "sync_auto_streams",
        sa.Column("stream_name", sa.String(30), primary_key=True),
        sa.Column(
            "config_id",
            sa.String(30),
            sa.ForeignKey("sync_auto_config.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    connection = op.get_bind()
    config_exists = connection.scalar(
        sa.text("SELECT count(*) FROM sync_auto_config WHERE id='master-data'")
    )
    if config_exists:
        current = datetime.now(UTC)
        connection.execute(
            table.insert(),
            [
                {
                    "stream_name": stream,
                    "config_id": "master-data",
                    "enabled": False,
                    "updated_at": current,
                }
                for stream in STREAMS
            ],
        )
        connection.execute(
            sa.text("UPDATE sync_auto_config SET enabled=false, next_run_at=NULL")
        )


def downgrade():
    op.drop_table("sync_auto_streams")
