from sqlalchemy import create_engine, inspect, pool
from sqlalchemy.engine import make_url

from alembic import context
from data_sync_etl.config import Settings

config = context.config
settings = Settings()
url = config.attributes.get("database_url") or settings.source_migration_database_url
if config.attributes.get("connection") is None and not url:
    raise RuntimeError("SOURCE_MIGRATION_DATABASE_URL is required; Master URL is never used")
if url and make_url(url).get_backend_name() != "postgresql":
    raise RuntimeError("Source migrations require PostgreSQL")
if url and config.attributes.get("connection") is None:
    source_url, master_url = make_url(url), make_url(settings.database_url)
    if (source_url.host, source_url.port or 5432, source_url.database) == (
        master_url.host,
        master_url.port or 5432,
        master_url.database,
    ):
        raise RuntimeError("SOURCE migration database must be separate from Master")


def run(connection):
    if connection.dialect.name != "postgresql":
        raise RuntimeError("Source migrations require PostgreSQL")
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" in tables or "master_learning_resources" in tables:
        raise RuntimeError("Refusing SOURCE migrations on a Master schema")
    context.configure(connection=connection, version_table="alembic_source_version")
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, version_table="alembic_source_version")
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    run(config.attributes["connection"])
else:
    engine = create_engine(
        url, poolclass=pool.NullPool, hide_parameters=True, connect_args={"connect_timeout": 5}
    )
    try:
        with engine.begin() as connection:
            run(connection)
    finally:
        engine.dispose()
