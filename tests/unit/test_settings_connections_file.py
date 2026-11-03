import json

import pytest

from data_sync_etl.config import Settings


@pytest.fixture(autouse=True)
def isolated_connection_environment(monkeypatch):
    for name in ('CONNECTIONS_FILE', 'ETL_CONNECTIONS_FILE', 'DATABASE_URL',
                 'SOURCE_DATABASE_URL', 'MONGO_URI', 'MONGO_DATABASE'):
        monkeypatch.delenv(name, raising=False)


def write_connections(path, *, mongo_uri='mongodb+srv://user:pass@atlas.example/etl_content'):
    path.write_text(json.dumps({
        'DATABASE_URL': 'postgresql+psycopg://runtime:pass@master.example/etl',
        'SOURCE_DATABASE_URL': 'postgresql+psycopg://reader:pass@source.example/Learning_Resources',
        'MONGO_URI': mongo_uri,
        'MONGO_DATABASE': 'etl_content',
    }), encoding='utf-8')


def test_private_connections_file_overrides_dotenv_connection_values(tmp_path, monkeypatch):
    connections = tmp_path / 'connections.json'
    write_connections(connections)
    env_file = tmp_path / '.env'
    env_file.write_text(f'CONNECTIONS_FILE={connections}\nMONGO_URI=mongodb://local.invalid\n', encoding='utf-8')
    monkeypatch.delenv('MONGO_URI', raising=False)

    settings = Settings(_env_file=env_file)

    assert settings.mongo_uri.startswith('mongodb+srv://')
    assert settings.mongo_database == 'etl_content'
    assert settings.database_url.startswith('postgresql+psycopg://runtime:')
    assert settings.source_database_url.endswith('/Learning_Resources')
    representation = str(settings)
    assert 'mongodb+srv://' not in representation
    assert 'postgresql+psycopg://' not in representation


def test_explicit_environment_and_init_values_override_private_file(tmp_path, monkeypatch):
    connections = tmp_path / 'connections.json'
    write_connections(connections)
    monkeypatch.setenv('MONGO_URI', 'mongodb://environment.invalid')
    settings = Settings(_env_file=None, connections_file=connections)
    assert settings.mongo_uri == 'mongodb://environment.invalid'

    settings = Settings(_env_file=None, connections_file=connections,
                        mongo_uri='mongodb://init.invalid')
    assert settings.mongo_uri == 'mongodb://init.invalid'


def test_host_dotenv_neon_and_master_urls_override_stale_file_endpoints(tmp_path, monkeypatch):
    connections = tmp_path / 'connections.json'
    write_connections(connections)
    env_file = tmp_path / '.env'
    env_file.write_text(
        f'CONNECTIONS_FILE={connections}\n'
        'DATABASE_URL=postgresql+psycopg://host:pass@127.0.0.1:5434/etl\n'
        'SOURCE_DATABASE_URL=postgresql+psycopg://reader:newpass@neon.example/Learning_Resources\n',
        encoding='utf-8')
    monkeypatch.delenv('DATABASE_URL', raising=False)
    monkeypatch.delenv('SOURCE_DATABASE_URL', raising=False)

    settings = Settings(_env_file=env_file)

    assert '@127.0.0.1:5434/etl' in settings.database_url
    assert '@neon.example/Learning_Resources' in settings.source_database_url
    assert settings.mongo_uri.startswith('mongodb+srv://')
