"""Create owned, isolated PostgreSQL QA resources; never inspect an app DB config."""

import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import psycopg


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / '.test-tmp/database-auth'
    output.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex[:10]
    name, network, volume = (f'etl-auth-test-{identifier}', f'etl-auth-test-net-{identifier}', f'etl-auth-test-data-{identifier}')
    password = secrets.token_urlsafe(32)
    private = Path(tempfile.mkdtemp(prefix='etl-auth-test-private-'))
    private.chmod(0o700)
    subprocess.run(['docker', 'network', 'create', '--label', 'etl.auth.test.owner=database-auth-20261022', network], check=True, capture_output=True)
    subprocess.run(['docker', 'volume', 'create', '--label', 'etl.auth.test.owner=database-auth-20261022', volume], check=True, capture_output=True)
    env = {**os.environ, 'POSTGRES_PASSWORD': password}
    subprocess.run(['docker', 'run', '-d', '--name', name, '--network', network,
        '--label', 'etl.auth.test.owner=database-auth-20261022', '-p', '127.0.0.1::5432',
        '-e', 'POSTGRES_PASSWORD', '-e', 'POSTGRES_DB=etl_auth_test',
        '--mount', f'type=volume,source={volume},target=/var/lib/postgresql/data', 'postgres:16'],
        env=env, check=True, capture_output=True)
    metadata = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
    port = int(metadata['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'])
    url = f'postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/etl_auth_test'
    for filename, value in [('owner-url', url), ('container-url', f'postgresql+psycopg://postgres:{password}@{name}:5432/etl_auth_test')]:
        path = private / filename
        path.write_text(value, encoding='ascii')
        path.chmod(0o600)
    state = {'container': name, 'network': network, 'volume': volume, 'private_directory': str(private), 'port': port}
    (output / 'qa-resources.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    ready = False
    for _ in range(90):
        try:
            with psycopg.connect(url.replace('postgresql+psycopg://', 'postgresql://'), connect_timeout=2):
                ready = True
                break
        except psycopg.OperationalError:
            time.sleep(1)
    if not ready:
        raise RuntimeError('ISOLATED_AUTH_POSTGRES_NOT_READY')
    env = {**os.environ, 'ETL_AUTH_TEST_DATABASE_URL': url,
           'ETL_AUTH_TEST_ISOLATED_ACK': 'database-auth-20261022'}
    result = subprocess.run([sys.executable, '-m', 'pytest', 'tests/unit/test_database_auth.py', '-q', '--tb=short',
                            '--basetemp=.test-tmp/database-auth/pg-pytest'], env=env, cwd=root,
                            capture_output=True, text=True)
    (output / 'postgres-tests.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    print(result.stdout, flush=True)
    if result.returncode:
        return result.returncode
    cli = [sys.executable, '-m', 'data_sync_etl.security.auth_db_cli']
    def maintenance(action, *args, stdin=None):
        check = subprocess.run(cli + [action, '--url-file', str(private / 'owner-url'), *args],
            input=stdin, text=True, capture_output=True, cwd=root)
        if check.returncode:
            raise RuntimeError('ISOLATED_AUTH_CLI_FAILED: ' + action)
        return check.stdout
    assert 'true' in maintenance('migrate', '--confirm-auth-maintenance')
    assert 'false' in maintenance('migrate', '--confirm-auth-maintenance')
    admin_password = secrets.token_urlsafe(24)
    assert 'true' in maintenance('create-admin', '--email', 'admin@etl.test', '--password-stdin',
                                 '--confirm-auth-maintenance', stdin=admin_password + '\n')
    assert 'false' in maintenance('create-admin', '--email', 'admin@etl.test', '--password-stdin',
                                  '--confirm-auth-maintenance', stdin=secrets.token_urlsafe(24) + '\n')
    maintenance('check-schema')
    # Restart the test DB's container and verify the same account/hash survives.
    subprocess.run(['docker', 'restart', '-t', '60', name], check=True, capture_output=True)
    # Docker Desktop may allocate a different ephemeral host port after restart.
    metadata = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
    port = int(metadata['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'])
    (private / 'owner-url').write_text(f'postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/etl_auth_test', encoding='ascii')
    state['port'] = port
    (output / 'qa-resources.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
    for _ in range(60):
        try:
            maintenance('check-schema')
            break
        except RuntimeError:
            time.sleep(1)
    else:
        raise RuntimeError('ISOLATED_AUTH_RESTART_FAILED')
    assert 'admin@etl.test' in maintenance('list-admin-users')
    (output / 'postgres-results.json').write_text(json.dumps({'status': 'PASS', 'checks': [
        'postgres_auth_tests', 'versioned_migration_idempotent', 'cli_bootstrap_idempotent',
        'postgres_restart_preserves_accounts'], 'resources': state}, indent=2), encoding='utf-8')
    print('PASS: isolated PostgreSQL migration, CLI bootstrap, RBAC and restart persistence. QA resources retained.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
