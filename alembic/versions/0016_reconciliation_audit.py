"""Add file size metadata and reconciliation audit tables.

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-23
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

    # 1. Bổ sung trường kích thước byte vào bảng resource_versions và source_file_imports
    op.add_column("resource_versions", sa.Column("source_file_size", sa.BigInteger(), nullable=True))
    op.add_column("resource_versions", sa.Column("storage_file_size", sa.BigInteger(), nullable=True))
    op.add_column("source_file_imports", sa.Column("source_file_size", sa.BigInteger(), nullable=True))
    op.add_column("source_file_imports", sa.Column("storage_file_size", sa.BigInteger(), nullable=True))

    # 2. Tạo bảng sync_reconciliation_runs lưu lịch sử từng đợt đối soát
    op.create_table(
        "sync_reconciliation_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("stream_name", sa.String(50), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("target_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mismatched_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source_total_bytes", sa.BigInteger(), nullable=True, server_default="0"),
        sa.Column("target_total_bytes", sa.BigInteger(), nullable=True, server_default="0"),
        sa.Column("source_table_hash", sa.String(64), nullable=True),
        sa.Column("target_table_hash", sa.String(64), nullable=True),
        sa.Column("summary_meta", json_type, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_reconcile_runs_stream_started",
        "sync_reconciliation_runs",
        ["stream_name", sa.text("started_at DESC")],
    )

    # 3. Tạo bảng sync_reconciliation_discrepancies lưu chi tiết các bản ghi bị lệch
    op.create_table(
        "sync_reconciliation_discrepancies",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("sync_reconciliation_runs.run_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("record_id", sa.String(100), nullable=False),
        sa.Column("stream_name", sa.String(50), nullable=False),
        sa.Column("discrepancy_flag", sa.String(50), nullable=False),
        sa.Column("source_value", json_type, nullable=True),
        sa.Column("target_value", json_type, nullable=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolution_status", sa.String(30), nullable=False, server_default="UNRESOLVED"),
    )
    op.create_index(
        "ix_reconcile_discrepancies_run_flag",
        "sync_reconciliation_discrepancies",
        ["run_id", "discrepancy_flag"],
    )
    op.create_index(
        "ix_reconcile_discrepancies_stream_status",
        "sync_reconciliation_discrepancies",
        ["stream_name", "resolution_status"],
    )
    op.create_index(
        "ix_reconcile_discrepancies_record",
        "sync_reconciliation_discrepancies",
        ["record_id"],
    )


def downgrade():
    op.drop_index("ix_reconcile_discrepancies_record", table_name="sync_reconciliation_discrepancies")
    op.drop_index("ix_reconcile_discrepancies_stream_status", table_name="sync_reconciliation_discrepancies")
    op.drop_index("ix_reconcile_discrepancies_run_flag", table_name="sync_reconciliation_discrepancies")
    op.drop_table("sync_reconciliation_discrepancies")

    op.drop_index("ix_reconcile_runs_stream_started", table_name="sync_reconciliation_runs")
    op.drop_table("sync_reconciliation_runs")

    op.drop_column("source_file_imports", "storage_file_size")
    op.drop_column("source_file_imports", "source_file_size")
    op.drop_column("resource_versions", "storage_file_size")
    op.drop_column("resource_versions", "source_file_size")
