import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import event, text

from alembic import command
from data_sync_etl.adapters.source.postgresql import TABLES, PostgreSQLSourceAdapter
from data_sync_etl.db.session import make_engine
from scripts.source_seed import seed_demo
from scripts.verify import validate_test_url

ROOT = Path(__file__).parents[3]


def migrate_source(engine, revision="head", downgrade=False):
    config = Config(str(ROOT / "alembic_source.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.fixture
def source_database():
    value = os.environ.get("TEST_SOURCE_DATABASE_URL")
    if not value:
        if os.environ.get("ETL_REQUIRE_SOURCE_POSTGRES") == "1":
            pytest.fail("TEST_SOURCE_DATABASE_URL required", pytrace=False)
        pytest.skip("TEST_SOURCE_DATABASE_URL missing; real SOURCE PostgreSQL required")
    url = validate_test_url(value, os.environ)
    if "test" not in url.database.split("_"):
        pytest.fail("Source tests require an explicitly named test database", pytrace=False)
    engine = make_engine(url)
    token = uuid4().hex
    schema, role = "etl_source_test_" + token, "etl_reader_test_" + token
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(
            text(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT')
        )

    @event.listens_for(engine, "checkout")
    def source_path(dbapi, record, proxy):
        old = dbapi.autocommit
        dbapi.autocommit = True
        with dbapi.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        dbapi.autocommit = old

    reader_engine = None
    try:
        migrate_source(engine)
        with engine.begin() as connection:
            connection.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            for table in TABLES.values():
                connection.execute(text(f'GRANT SELECT ON "{schema}"."{table}" TO "{role}"'))
        reader_engine = make_engine(url)

        @event.listens_for(reader_engine, "checkout")
        def reader_path(dbapi, record, proxy):
            old = dbapi.autocommit
            dbapi.autocommit = True
            with dbapi.cursor() as cursor:
                cursor.execute(f'SET search_path TO "{schema}"')
                cursor.execute(f'SET ROLE "{role}"')
            dbapi.autocommit = old

        yield SimpleNamespace(
            engine=engine,
            schema=schema,
            database=url.database,
            role=role,
            reader_engine=reader_engine,
            adapter=PostgreSQLSourceAdapter(engine=reader_engine),
        )
    finally:
        if reader_engine is not None:
            reader_engine.dispose()
        with engine.begin() as connection:
            assert schema.startswith("etl_source_test_") and role.startswith("etl_reader_test_")
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            database = connection.dialect.identifier_preparer.quote(url.database)
            connection.execute(text(f'REVOKE CONNECT ON DATABASE {database} FROM "{role}"'))
            connection.execute(text(f'DROP ROLE "{role}"'))
        engine.dispose()


@pytest.fixture
def source(source_database):
    with source_database.engine.begin() as connection:
        seed_demo(connection, ROOT / "fixtures/source")
    return source_database
