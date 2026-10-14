"""Approved diagram references; no source-ID copy or automatic historical backfill."""

import sqlalchemy as sa

from alembic import op

revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None
REFERENCES = (('master_grade_levels', 'grade_mongo_id', 24),
              ('resource_versions', 'mongo_id', 30), ('content_units', 'mongo_id', 30))


def upgrade():
    for table, column, length in REFERENCES:
        op.add_column(table, sa.Column(column, sa.String(length), nullable=True))
        if column == 'mongo_id':
            op.create_index('uq_' + table + '_mongo_id', table, [column], unique=True)


def downgrade():
    for table, column, _ in REFERENCES:
        if op.get_bind().scalar(sa.text(f'SELECT count(*) FROM {table} WHERE {column} IS NOT NULL')):
            raise RuntimeError('Cannot downgrade diagram references while mappings exist')
    for table, column, _ in reversed(REFERENCES):
        if column == 'mongo_id':
            op.drop_index('uq_' + table + '_mongo_id', table_name=table)
        op.drop_column(table, column)
