"""Durable source version mapping and file import queue."""

import sqlalchemy as sa

from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "source_file_imports",
        sa.Column("id", sa.String(30), primary_key=True),
        sa.Column("source_system", sa.String(30), nullable=False),
        sa.Column("source_version_id", sa.String(30), nullable=False),
        sa.Column("resource_id", sa.String(30), nullable=False),
        sa.Column("resource_title", sa.String(500)),
        sa.Column("source_version_number", sa.Integer(), nullable=False),
        sa.Column("source_lifecycle_status", sa.String(30), nullable=False),
        sa.Column("source_resource_active", sa.Boolean(), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("storage_bucket", sa.String(255), nullable=False),
        sa.Column("storage_object_key", sa.String(500), nullable=False),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("manual_retries", sa.Integer(), nullable=False),
        sa.Column("processing_retry_requested", sa.Boolean(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(50)),
        sa.Column("claim_token", sa.String(30)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("target_version_id", sa.String(30), sa.ForeignKey("resource_versions.id", ondelete="RESTRICT")),
        sa.Column("processing_job_id", sa.String(30), sa.ForeignKey("ocr_jobs.id", ondelete="RESTRICT")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_system", "source_version_id", name="uq_source_file_identity"),
        sa.UniqueConstraint("target_version_id", name="uq_source_file_target"),
    )
    op.create_index("ix_source_file_queue", "source_file_imports", ["status", "next_retry_at", "lease_expires_at"])


def downgrade():
    op.drop_index("ix_source_file_queue", table_name="source_file_imports")
    op.drop_table("source_file_imports")
