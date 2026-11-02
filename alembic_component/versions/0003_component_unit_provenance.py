"""Track component-only unit provenance and postprocessing manifests."""
from sqlalchemy import JSON, Column, Integer, String

from alembic import op

revision = 'component_0003'
down_revision = 'component_0002'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'component_ocr_unit_provenance',
        Column('namespace', String(255), primary_key=True),
        Column('source_unit_id', String(255), primary_key=True),
        Column('generation', Integer, primary_key=True),
        Column('source_version_id', String(255), nullable=False),
        Column('source_task_id', String(255), nullable=False),
        Column('page_num', Integer, nullable=False),
        Column('title', String(500)),
        Column('source_locator', JSON),
    )
    op.create_table(
        'component_ocr_postprocess_manifests',
        Column('namespace', String(255), primary_key=True),
        Column('source_version_id', String(255), primary_key=True),
        Column('generation', Integer, primary_key=True),
        Column('source_job_id', String(255), nullable=False),
        Column('source_hash', String(64), nullable=False),
        Column('processing_fingerprint', String(64), nullable=False),
        Column('content_fingerprint', String(64), nullable=False),
        Column('unit_count', Integer, nullable=False),
    )


def downgrade():
    raise RuntimeError('Component OCR units require a reviewed data-preserving rollback')
