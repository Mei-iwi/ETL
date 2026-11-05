import json
import runpy
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / 'deploy/scripts/materialize-env-secrets.py'
materialize = runpy.run_path(str(MODULE))['materialize']


def test_runtime_credentials_can_be_rebuilt_from_env_alone(tmp_path):
    values = {
        'ETL_CONNECTIONS_JSON': json.dumps({
            'DATABASE_URL': 'postgresql://test@master-postgres/test',
            'SOURCE_DATABASE_URL': 'postgresql://test@source.example/test',
            'MONGO_URI': 'mongodb://host.docker.internal:27017/', 'MONGO_DATABASE': 'etl_content',
        }),
        'AUTH_PROXY_KEY': 'a' * 64, 'ADMIN_PASSWORD_HASH': 'test-only-hash',
        'SOURCE_WRITE_DATABASE_URL': 'postgresql://writer:test-only@source.example/test',
        'NEO4J_AUTH': 'neo4j/test-only-password',
    }
    assert materialize(values, tmp_path) == len(values)
    output = tmp_path / 'ETL_Secrets/docker/runtime-env-secrets'
    assert (output / 'auth_proxy_key.secret').read_text() == values['AUTH_PROXY_KEY']
    assert json.loads((output / 'connections.json').read_text())['MONGO_DATABASE'] == 'etl_content'
    old_mtime = (output / 'auth_proxy_key.secret').stat().st_mtime_ns
    materialize(values, tmp_path)
    assert (output / 'auth_proxy_key.secret').stat().st_mtime_ns == old_mtime
    values['AUTH_PROXY_KEY'] = 'b' * 64
    materialize(values, tmp_path)
    assert (output / 'auth_proxy_key.secret').read_text() == 'b' * 64


@pytest.mark.parametrize('payload', ['not-json', '{}', '[]', '{"DATABASE_URL":"secret"}'])
def test_invalid_connection_payload_never_writes_partial_secrets(tmp_path, payload):
    with pytest.raises((ValueError, TypeError)):
        materialize({'AUTH_PROXY_KEY': 'a' * 64, 'ETL_CONNECTIONS_JSON': payload}, tmp_path)
    assert not list(tmp_path.rglob('*.secret'))


def test_runtime_artifacts_cannot_escape_deployment(tmp_path):
    with pytest.raises(ValueError):
        materialize({'ENV_SECRETS_DIR': str(tmp_path.parent / 'outside'), 'AUTH_PROXY_KEY': 'test'}, tmp_path)


def test_direct_database_env_is_the_single_editable_connection_source(tmp_path):
    values = {'DATABASE_URL': 'postgresql://test@master-postgres/test',
              'SOURCE_DATABASE_URL': 'postgresql://test@source.example/test',
              'MONGO_URI': 'mongodb://host.docker.internal:27017/', 'MONGO_DATABASE': 'etl_content'}
    assert materialize(values, tmp_path) == 1
    output = tmp_path / 'ETL_Secrets/docker/runtime-env-secrets/connections.json'
    assert json.loads(output.read_text()) == values
    values['MONGO_URI'] = 'mongodb://other.example:27017/'
    materialize(values, tmp_path)
    assert json.loads(output.read_text()) == values


def test_incomplete_or_conflicting_connection_copies_fail_before_writing(tmp_path):
    with pytest.raises(ValueError, match='PRIVATE_ENV_CONNECTIONS_INCOMPLETE'):
        materialize({'DATABASE_URL': 'postgresql://test@master-postgres/test'}, tmp_path)
    values = {'DATABASE_URL': 'postgresql://test@master-postgres/test',
              'SOURCE_DATABASE_URL': 'postgresql://test@source.example/test',
              'MONGO_URI': 'mongodb://host.docker.internal:27017/', 'MONGO_DATABASE': 'etl_content',
              'ETL_CONNECTIONS_JSON': '{}'}
    with pytest.raises(ValueError, match='PRIVATE_ENV_CONNECTIONS_CONFLICT'):
        materialize(values, tmp_path)
    assert not list(tmp_path.rglob('connections.json'))
