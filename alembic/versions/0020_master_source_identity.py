"""Add additive, auditable SOURCE identity locators to MASTER tables."""

import sqlalchemy as sa

from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

TABLES = (
    "master_users",
    "master_learning_resources",
    "master_grade_levels",
    "subjects",
)
COLUMNS = (
    sa.Column("source_identity_version", sa.SmallInteger(), nullable=True),
    sa.Column("source_backend", sa.String(64), nullable=True),
    sa.Column("source_namespace", sa.String(255), nullable=True),
    sa.Column("source_entity_type", sa.String(64), nullable=True),
    sa.Column("source_record_key", sa.String(255), nullable=True),
    sa.Column("source_identity_hash", sa.String(64), nullable=True),
)


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table in TABLES:
        existing = {column["name"] for column in inspector.get_columns(table)}
        for column in COLUMNS:
            if column.name not in existing:
                op.add_column(
                    table,
                    sa.Column(column.name, column.type, nullable=column.nullable),
                )
        index_name = f"uq_{table}_source_identity"
        if index_name not in {index["name"] for index in inspector.get_indexes(table)}:
            op.create_index(
                index_name,
                table,
                [
                    "source_namespace",
                    "source_backend",
                    "source_entity_type",
                    "source_record_key",
                ],
                unique=True,
            )


def downgrade():
    connection = op.get_bind()
    for table in TABLES:
        if connection.scalar(
            sa.text(
                f"SELECT count(*) FROM {table} WHERE source_identity_hash IS NOT NULL"
            )
        ):
            raise RuntimeError("SOURCE_IDENTITY_DATA_PRESERVED: export mappings before downgrade")
    for table in reversed(TABLES):
        index_name = f"uq_{table}_source_identity"
        if index_name in {index["name"] for index in sa.inspect(connection).get_indexes(table)}:
            op.drop_index(index_name, table_name=table)
        for column in reversed(COLUMNS):
            op.drop_column(table, column.name)
