"""Explicit bootstrap of this test compose's empty databases ONLY."""
import os
from pathlib import Path

from alembic.config import Config
from pymongo import MongoClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from alembic import command
from data_sync_etl.adapters.mongo.schema import initialize


def main():
    if os.environ.get('ETL_APPROVE_EMPTY_TEST_BOOTSTRAP') != 'docker-portable-20261021':
        raise RuntimeError('ISOLATED_TEST_ACK_REQUIRED')
    expected = [('TEST_DATABASE_URL', 'master-test', 'etl_test_portable_master', 'alembic.ini'),
                ('TEST_SOURCE_DATABASE_URL', 'source-test', 'etl_test_portable_source', 'alembic_source.ini')]
    for key, host, database, ini in expected:
        url = make_url(os.environ[key])
        assert url.host == host and url.database == database
        engine = create_engine(url)
        assert not inspect(engine).get_table_names(), 'Test bootstrap refuses existing schema'
        with engine.begin() as connection:
            config = Config(str(Path('/app') / ini))
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
            role = 'etl_test_runtime' if host == 'master-test' else 'etl_test_reader'
            # psycopg bind parameters are used for values; role names are constants.
            from psycopg import sql
            driver = connection.connection.driver_connection
            with driver.cursor() as cursor:
                cursor.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(
                    sql.Identifier(role), sql.Literal(os.environ['TEST_RUNTIME_PASSWORD'])))
            connection.execute(text(f'GRANT CONNECT ON DATABASE {database} TO {role}'))
            connection.execute(text(f'GRANT USAGE ON SCHEMA public TO {role}'))
            privileges = 'SELECT, INSERT, UPDATE, DELETE' if host == 'master-test' else 'SELECT'
            connection.execute(text(f'GRANT {privileges} ON ALL TABLES IN SCHEMA public TO {role}'))
            if host == 'source-test':
                connection.execute(text(f'ALTER ROLE {role} SET default_transaction_read_only = on'))
        engine.dispose()
    with MongoClient(os.environ['TEST_MONGO_URI']) as client:
        db = client['etl_test_portable_content']
        assert not db.list_collection_names(), 'Test bootstrap refuses nonempty Mongo'
        initialize(db, dimension=384, apply=True)
        db.command('createUser', 'etl_test_runtime', pwd=os.environ['TEST_RUNTIME_PASSWORD'],
                   roles=[{'role': 'readWrite', 'db': db.name}])
    print('PASS: isolated test schemas bootstrapped, SOURCE runtime role read-only')


if __name__ == '__main__':
    main()
