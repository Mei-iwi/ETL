"""Nullable logical Mongo references; no inference/backfill or cross-database FK."""

import sqlalchemy as sa

from alembic import op

revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None
TABLES = ('master_users', 'master_learning_resources')


def upgrade():
    for table in TABLES:
        op.add_column(table, sa.Column('mongo_id', sa.String(30), nullable=True))
        # PostgreSQL/SQLite UNIQUE both permit multiple NULLs, but no duplicate reference.
        op.create_index('uq_' + table + '_mongo_id', table, ['mongo_id'], unique=True)


def downgrade():
    # Never silently discard mappings, even in a developer-requested downgrade.
    for table in TABLES:
        if op.get_bind().scalar(sa.text(f'SELECT count(*) FROM {table} WHERE mongo_id IS NOT NULL')):
            raise RuntimeError('Cannot downgrade Master Mongo references while mappings exist')
    for table in TABLES:
        op.drop_index('uq_' + table + '_mongo_id', table_name=table)
        op.drop_column(table, 'mongo_id')
