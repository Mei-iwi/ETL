"""Add isolated, text-free component OCR read models to Master.

Revision ID: 0021
Revises: 0020
"""
import os

from sqlalchemy import inspect, text

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
    # This revision remains recoverable for an explicitly reviewed rollback of
    # the rollback, but routine `upgrade head` must not recreate these tables.
    if os.environ.get('ETL_APPROVE_COMPONENT_OCR_0021') != 'APPROVED_DATABASE_MAINTENANCE':
        raise RuntimeError('Revision 0021 is disabled; use MASTER revision 0020')
    for model in (ComponentJobRef, ComponentPageRef, ComponentUnitRef):
        model.__table__.create(op.get_bind(), checkfirst=False)


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql' or not bind.in_transaction():
        raise RuntimeError('Component OCR rollback requires a transactional PostgreSQL MASTER')
    tables = ('master_component_ocr_jobs', 'master_component_ocr_pages', 'master_component_content_units')
    inspector = inspect(bind)
    if any(not inspector.has_table(name, schema='public') for name in tables):
        raise RuntimeError('Component OCR rollback requires all three revision 0021 tables')
    # Acquire exclusive locks before counting so no concurrent writer can add
    # data between the emptiness check and DROP. PostgreSQL holds these locks
    # until the Alembic transaction commits or rolls back.
    for name in tables:
        bind.execute(text(f'LOCK TABLE public.{name} IN ACCESS EXCLUSIVE MODE'))
    for name in tables:
        if bind.execute(text(f'SELECT EXISTS (SELECT 1 FROM public.{name})')).scalar():
            raise RuntimeError('Component OCR rollback refused: a read model contains data')
    external_fk = bind.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM pg_constraint c
            WHERE c.contype = 'f'
              AND c.confrelid IN (
                'public.master_component_ocr_jobs'::regclass,
                'public.master_component_ocr_pages'::regclass,
                'public.master_component_content_units'::regclass
              )
              AND c.conrelid NOT IN (
                'public.master_component_ocr_jobs'::regclass,
                'public.master_component_ocr_pages'::regclass,
                'public.master_component_content_units'::regclass
              )
        )
    """)).scalar()
    if external_fk:
        raise RuntimeError('Component OCR rollback refused: external foreign key exists')
    # DROP uses PostgreSQL RESTRICT (no CASCADE), which also rejects any
    # dependent views or other objects not covered by the foreign-key check.
    for name in reversed(tables):
        op.drop_table(name, schema='public')
