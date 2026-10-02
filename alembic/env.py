from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

from alembic import context
from data_sync_etl.config import Settings
from data_sync_etl.db.models import Base

config = context.config
url = config.attributes.get("database_url") or Settings().database_url


def run(connection):
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    options = {"connect_timeout": 5} if make_url(url).get_backend_name() == "postgresql" else {}
    engine = create_engine(url, poolclass=pool.NullPool, hide_parameters=True, connect_args=options)
    with engine.connect() as connection:
        run(connection)
