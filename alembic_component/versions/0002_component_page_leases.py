"""Add fenced component OCR page leases without changing Master or SOURCE."""
from sqlalchemy import Column, DateTime, Integer, String

from alembic import op

revision = 'component_0002'
down_revision = 'component_0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('uq_component_ocr_version_generation', 'component_ocr_jobs',
                    ['namespace', 'source_version_id', 'generation'], unique=True)
    op.create_table(
        'component_ocr_page_leases',
        Column('namespace', String(255), primary_key=True),
        Column('source_task_id', String(255), primary_key=True),
        Column('generation', Integer, primary_key=True),
        Column('worker_id', String(100)),
        Column('claim_token', String(30)),
        Column('lease_expires_at', DateTime(timezone=True)),
        Column('heartbeat_at', DateTime(timezone=True)),
        Column('next_retry_at', DateTime(timezone=True)),
        Column('attempts', Integer, nullable=False, server_default='0'),
        Column('last_error_code', String(80)),
    )


def downgrade():
    raise RuntimeError('Component OCR leases require a reviewed data-preserving rollback')
