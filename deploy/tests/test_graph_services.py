import os

import pytest
import yaml
from test_runtime import ROOT, load_runtime


@pytest.mark.parametrize('command,role,extra', [
    ('run-users-refresh-worker', 'users-refresh', ['--source-id', 'postgresql']),
    ('run-graph-worker', 'graph', []),
])
def test_supervised_commands_use_official_cli_without_worker_id(monkeypatch, command, role, extra):
    runtime = load_runtime('runtime')
    roles, calls = [], []

    class Executed(BaseException):
        pass

    def execute(binary, arguments):
        calls.append((binary, arguments))
        raise Executed()

    monkeypatch.setattr(runtime, 'verify_runtime', roles.append)
    monkeypatch.setattr(runtime.os, 'execvp', execute)
    monkeypatch.setattr(runtime.sys, 'argv', ['runtime.py', 'graph-services-worker', command])
    monkeypatch.setenv('USERS_REFRESH_SOURCE_ID', 'postgresql')
    with pytest.raises(Executed):
        runtime.main()
    assert roles == [role]
    assert calls == [('python', ['python', '-m', 'data_sync_etl.cli', command, *extra])]
    assert os.environ['ETL_SERVICE_ROLE'] == role


def test_opt_in_workers_have_no_default_start_or_source_write_capability():
    data = yaml.safe_load((ROOT / 'deploy/compose.graph-workers.yml').read_text())
    assert set(data['services']) == {'users-refresh-worker', 'graph-worker'}
    for service in data['services'].values():
        assert service['profiles'] == ['graph-services']
        assert service['extends']['service'] == 'master-sync-worker'
        assert service['healthcheck']['test'][-1] == 'health-worker'
        for key in ('SOURCE_METADATA_WRITEBACK_ENABLED', 'SOURCE_VERSION_WRITEBACK_ENABLED',
                    'SOURCE_STORAGE_WRITE_ENABLED'):
            assert service['environment'][key] == 'false'
    graph = data['services']['graph-worker']
    assert graph['environment']['NEO4J_SEARCH_ENABLED'] == 'false'
    assert graph['volumes'][0]['bind']['create_host_path'] is False
