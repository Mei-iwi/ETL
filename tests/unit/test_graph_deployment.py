import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_graph_launcher_can_validate_env_without_an_auth_file(tmp_path, monkeypatch):
    env = tmp_path / 'graph.env'
    env.write_text('NEO4J_AUTH=neo4j/test-only-password\nNEO4J_PASSWORD=test-only-password\n', encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['check-neo4j-config.py', '--env-file', str(env)])
    module = runpy.run_path(str(ROOT / 'deploy/scripts/check-neo4j-config.py'))
    module['main']()
    env.write_text('NEO4J_AUTH=neo4j/test-only-password\nNEO4J_PASSWORD=different-test-only-password\n', encoding='utf-8')
    with pytest.raises(SystemExit, match='GRAPH_PRIVATE_RUNTIME_CONFIG_INVALID'):
        module['main']()


@pytest.mark.parametrize(('key', 'value', 'code'), [
    ('NEO4J_BIND_ADDRESS', '0.0.0.0', 'GRAPH_LOOPBACK_BIND_REQUIRED'),
    ('NEO4J_DOCKER_IMAGE', 'neo4j:latest', 'GRAPH_PINNED_COMMUNITY_IMAGE_REQUIRED'),
    ('NEO4J_BOLT_PORT', '7474', 'GRAPH_PRIVATE_RUNTIME_CONFIG_INVALID'),
    ('NEO4J_BOLT_PORT', 'invalid', 'GRAPH_PRIVATE_RUNTIME_CONFIG_INVALID'),
])
def test_deployment_guard_rejects_exposure_unpinned_image_and_ports(tmp_path, monkeypatch, key, value, code):
    secret = tmp_path / 'auth.secret'
    secret.write_text('neo4j/test-only-password', encoding='utf-8')
    env = tmp_path / 'graph.env'
    env.write_text(f'NEO4J_AUTH_FILE={secret.as_posix()}\n{key}={value}\n', encoding='utf-8')
    monkeypatch.setattr('sys.argv', ['check-neo4j-config.py', '--env-file', str(env)])
    module = runpy.run_path(str(ROOT / 'deploy/scripts/check-neo4j-config.py'))
    with pytest.raises(SystemExit, match=code):
        module['main']()


def test_prepare_uses_private_files_and_never_rotates_existing_secret(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    secret = tmp_path / 'private' / 'auth.secret'
    env = tmp_path / '.env'
    env.write_text('NEO4J_USER=neo4j\nNEO4J_PASSWORD=test-only-password\nNEO4J_AUTH_FILE='+secret.as_posix(), encoding='utf-8')
    module = runpy.run_path(str(ROOT / 'deploy/scripts/prepare-neo4j.py'))
    module['main']()
    assert secret.read_text(encoding='utf-8') == 'neo4j/test-only-password'
    assert secret.with_name('password.secret').read_text(encoding='utf-8') == 'test-only-password'
    assert 'test-only-password' not in capsys.readouterr().out
    env.write_text(env.read_text().replace('test-only-password', 'different-test-password'), encoding='utf-8')
    with pytest.raises(SystemExit, match='GRAPH_AUTH_FILE_DIFFERS_NO_AUTOMATIC_ROTATION'):
        module['main']()
    assert secret.read_text() == 'neo4j/test-only-password'
