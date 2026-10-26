"""MASTER visibility override and durable deletion audit/outbox. No SOURCE DDL."""
import sqlalchemy as sa

from alembic import op

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('master_learning_resources', sa.Column('master_deleted_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('master_learning_resources', sa.Column('master_deleted_by', sa.String(255), nullable=True))
    op.add_column('master_learning_resources', sa.Column('master_deletion_reason', sa.String(500), nullable=True))
    op.add_column('master_learning_resources', sa.Column('deletion_revision', sa.Integer(), nullable=False, server_default='0'))
    op.create_table('resource_deletion_events',
        sa.Column('id', sa.String(30), primary_key=True),
        sa.Column('resource_id', sa.String(30), sa.ForeignKey('master_learning_resources.id'), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(10), nullable=False),
        sa.Column('actor_id', sa.String(255), nullable=False),
        sa.Column('reason', sa.String(500), nullable=True),
        sa.Column('origin', sa.String(30), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('propagated_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('resource_id', 'revision', name='uq_resource_deletion_revision'),
        sa.CheckConstraint("action IN ('DELETE','RESTORE')", name='ck_resource_deletion_action'),
        sa.CheckConstraint('revision > 0', name='ck_resource_deletion_positive_revision'))
    op.create_index('ix_resource_deletion_pending', 'resource_deletion_events', ['propagated_at', 'created_at'])


def downgrade():
    connection = op.get_bind()
    if (connection.scalar(sa.text('SELECT count(*) FROM resource_deletion_events'))
            or connection.scalar(sa.text('SELECT count(*) FROM master_learning_resources WHERE deletion_revision <> 0 OR master_deleted_at IS NOT NULL'))):
        raise RuntimeError('SOFT_DELETE_ROLLBACK_REQUIRES_ARCHIVED_AUDIT_AND_APPROVAL')
    op.drop_index('ix_resource_deletion_pending', table_name='resource_deletion_events')
    op.drop_table('resource_deletion_events')
    for name in ('deletion_revision', 'master_deletion_reason', 'master_deleted_by', 'master_deleted_at'):
        op.drop_column('master_learning_resources', name)
