"""Frozen schema revision 0004."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None
TABLES = ["etl_stage_runs", "content_unit_texts"]


def metadata():
    spec = spec_from_file_location("etl_frozen_schema", Path(__file__).parents[1] / "schema_v1.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Base.metadata


def upgrade():
    frozen = metadata()
    for name in TABLES:
        frozen.tables[name].create(bind=op.get_bind(), checkfirst=False)


def downgrade():
    for name in reversed(TABLES):
        op.drop_table(name)
