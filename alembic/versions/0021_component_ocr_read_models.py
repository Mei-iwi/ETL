"""Add isolated, text-free component OCR read models to Master.

Revision ID: 0021
Revises: 0020
"""
from alembic import op
from data_sync_etl.db.ocr_component_models import (
    ComponentJobRef,
    ComponentPageRef,
    ComponentUnitRef,
)

revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade():
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        model.__table__.create(op.get_bind(), checkfirst=False)


def downgrade():
    raise RuntimeError('Component OCR read models require a reviewed data-preserving rollback')
