"""Create component-owned OCR state and transactional outbox.

This revision is for a separately provisioned, verified isolated component
database only; it must never be applied to SOURCE or the Master database.
"""
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
)

from alembic import op

revision = 'component_0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'component_ocr_jobs',
        Column('namespace', String(255), primary_key=True),
        Column('source_job_id', String(255), primary_key=True),
        Column('generation', Integer, primary_key=True),
        Column('source_version_id', String(255), nullable=False),
        Column('master_version_id', String(30), nullable=False),
        Column('master_resource_id', String(30), nullable=False),
        Column('source_hash', String(64), nullable=False),
        Column('processing_fingerprint', String(64), nullable=False),
        Column('status', String(40), nullable=False),
        Column('total_pages', Integer, nullable=False),
        Column('completed_pages', Integer, nullable=False),
        Column('failed_pages', Integer, nullable=False),
        Column('revision', BigInteger, nullable=False),
        Column('tombstone', Boolean, nullable=False),
    )
    op.create_table(
        'component_ocr_pages',
        Column('namespace', String(255), primary_key=True),
        Column('source_task_id', String(255), primary_key=True),
        Column('generation', Integer, primary_key=True),
        Column('source_job_id', String(255), nullable=False),
        Column('master_version_id', String(30), nullable=False),
        Column('page_num', Integer, nullable=False),
        Column('status', String(40), nullable=False),
        Column('retry_count', Integer, nullable=False),
        Column('error_code', String(80)),
        Column('verified', Boolean, nullable=False),
        Column('revision', BigInteger, nullable=False),
        Column('tombstone', Boolean, nullable=False),
    )
    op.create_table(
        'component_content_units',
        Column('namespace', String(255), primary_key=True),
        Column('source_unit_id', String(255), primary_key=True),
        Column('generation', Integer, primary_key=True),
        Column('source_version_id', String(255), nullable=False),
        Column('master_version_id', String(30), nullable=False),
        Column('master_resource_id', String(30), nullable=False),
        Column('parent_unit_id', String(255)),
        Column('unit_type', String(40), nullable=False),
        Column('sequence_no', Integer, nullable=False),
        Column('page_from', Integer),
        Column('page_to', Integer),
        Column('review_status', String(40), nullable=False),
        Column('retrieval_eligible', Boolean, nullable=False),
        Column('text_checksum', String(64), nullable=False),
        Column('verified', Boolean, nullable=False),
        Column('revision', BigInteger, nullable=False),
        Column('tombstone', Boolean, nullable=False),
    )
    op.create_table(
        'component_ocr_outbox',
        Column('id', Integer, primary_key=True, autoincrement=True),
        Column('namespace', String(255), nullable=False),
        Column('kind', String(10), nullable=False),
        Column('entity_id', String(255), nullable=False),
        Column('generation', Integer, nullable=False),
        Column('revision', BigInteger, nullable=False),
        Column('snapshot', JSON, nullable=False),
        Column('delivered', Boolean, nullable=False),
        Column('attempts', Integer, nullable=False, server_default='0'),
        Column('next_attempt_at', DateTime(timezone=True)),
        Column('last_error_code', String(80)),
        UniqueConstraint('namespace', 'kind', 'entity_id', 'generation', 'revision',
                         name='uq_component_ocr_event'),
    )


def downgrade():
    raise RuntimeError('Component OCR data needs an explicit data-preserving rollback')
