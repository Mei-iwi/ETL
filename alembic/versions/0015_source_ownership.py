"""Explicit ownership; historical records remain UNKNOWN, no backfill."""
import sqlalchemy as sa

from alembic import op

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None
TABLES = ('master_users', 'master_learning_resources', 'subjects', 'master_grade_levels')


def upgrade():
    for table in TABLES:
        op.add_column(table, sa.Column('source_origin', sa.String(30), nullable=False, server_default='UNKNOWN'))
        op.add_column(table, sa.Column('source_state_hash', sa.String(64), nullable=True))
        op.add_column(table, sa.Column('source_missing_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for table in TABLES:
        reference = 'grade_mongo_id' if table == 'master_grade_levels' else 'mongo_id' if table != 'subjects' else None
        mapped = f' OR {reference} IS NOT NULL' if reference else ''
        if op.get_bind().scalar(sa.text(f"SELECT count(*) FROM {table} WHERE source_origin <> 'UNKNOWN' OR source_missing_at IS NOT NULL OR source_state_hash IS NOT NULL{mapped}")):
            raise RuntimeError('Cannot discard source ownership while mappings exist')
    for table in reversed(TABLES):
        for column in ('source_missing_at', 'source_state_hash', 'source_origin'):
            op.drop_column(table, column)
