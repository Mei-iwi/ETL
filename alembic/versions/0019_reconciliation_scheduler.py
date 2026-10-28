"""Add isolated audit leases and file verification evidence; no sync DDL."""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('reconciliation_audit_jobs',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('job_key', sa.String(150), nullable=False, unique=True),
        sa.Column('audit_type', sa.String(30), nullable=False),
        sa.Column('stream_name', sa.String(30), nullable=False),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('claim_token', sa.String(36)),
        sa.Column('lease_expires_at', sa.DateTime(timezone=True)),
        sa.Column('heartbeat_at', sa.DateTime(timezone=True)),
        sa.Column('next_retry_at', sa.DateTime(timezone=True)),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('checkpoint', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=False),
        sa.Column('error_code', sa.String(80)))
    op.create_index('ix_audit_jobs_due', 'reconciliation_audit_jobs', ['status', 'next_retry_at'])
    op.create_table('reconciliation_file_states',
        sa.Column('resource_version_id', sa.String(36), primary_key=True),
        sa.Column('last_verified_at', sa.DateTime(timezone=True)),
        sa.Column('last_verified_hash', sa.String(64)),
        sa.Column('last_verified_size', sa.BigInteger()),
        sa.Column('verification_method', sa.String(40)),
        sa.Column('observed_identity', sa.String(64)),
        sa.Column('last_observed_revision', sa.String(64)),
        sa.Column('last_attempt_at', sa.DateTime(timezone=True)),
        sa.Column('next_due_at', sa.DateTime(timezone=True)),
        sa.Column('status', sa.String(30), nullable=False),
        sa.Column('reason_code', sa.String(80)),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'))
    op.create_index('ix_audit_file_due', 'reconciliation_file_states', ['next_due_at', 'resource_version_id'])


def downgrade():
    # Schema-only rollback is possible before first use; never erase evidence.
    connection = op.get_bind()
    for name in ('reconciliation_file_states', 'reconciliation_audit_jobs'):
        if connection.scalar(sa.text(f'SELECT count(*) FROM {name}')):
            raise RuntimeError('AUDIT_HISTORY_PRESERVED: disable scheduler; retain audit tables')
    op.drop_table('reconciliation_file_states')
    op.drop_table('reconciliation_audit_jobs')
