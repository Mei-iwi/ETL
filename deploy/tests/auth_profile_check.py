"""Check real Compose merges and runtime auth startup without any DB connection."""
import json
import subprocess
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    scratch = root / '.test-tmp/docker-portable'
    docker = ['docker', '--config', str(scratch / 'docker-client')]
    backend_image = 'etl-portable-backend:auth-20261022'
    metadata = json.loads(subprocess.check_output(docker + ['image', 'inspect', backend_image], text=True))[0]
    assert 'AUTH_MODE=single_admin' in metadata['Config']['Env']
    default = subprocess.run(docker + ['run', '--rm', '--network', 'none', backend_image, 'api'], capture_output=True, text=True)
    assert default.returncode == 1 and 'AUTH_DEPLOYMENT_PROFILE_REQUIRED' in default.stderr
    legacy = subprocess.run(docker + ['run', '--rm', '--network', 'none', '-e', 'AUTH_MODE=legacy_local', '-e', 'ADMIN_ENABLED=true', backend_image, 'api'], capture_output=True, text=True)
    assert legacy.returncode == 1 and 'LOCAL_DEMO_GRANT_FORBIDDEN_IN_CONTAINER' in legacy.stderr
    private = scratch / 'auth-acceptance/private'
    profile = scratch / 'auth-profile-check'
    profile.mkdir(exist_ok=True)
    source = profile / 'source-empty'
    source.mkdir(exist_ok=True)
    # Connection contents are not loaded: Compose config only checks the path.
    connections = profile / 'unused-connections.json'
    connections.write_text('{}')
    env = {'APP_PROJECT': 'etl-auth-profile-check', 'BACKEND_IMAGE': 'etl-portable-backend:auth-20261022',
        'FRONTEND_IMAGE': 'etl-portable-frontend:auth-20261022', 'WEB_PORT': '18088',
        'MODEL_DIR': (root / 'Models/paraphrase-multilingual-MiniLM-L12-v2').as_posix(),
        'MODEL_MANIFEST_FILE': (scratch / 'model-manifest.json').as_posix(),
        'SOURCE_LOCAL_DIR': source.as_posix(), 'CONNECTIONS_FILE': connections.as_posix(),
        'ADMIN_LOGIN_EMAIL': 'admin@etl.test', 'AUTH_MODE': 'single_admin',
        'AUTH_ORIGIN': 'http://127.0.0.1:18088',
        'ADMIN_PASSWORD_HASH_SECRET_FILE': (private / 'admin_password_hash').as_posix(),
        'SESSION_SIGNING_KEY_SECRET_FILE': (private / 'session_signing_key').as_posix(),
        'AUTH_PROXY_KEY_SECRET_FILE': (private / 'auth_proxy_key').as_posix()}
    rows = [{'case': 'default-single-admin-without-secrets-fails-closed', 'status': 'PASS'},
            {'case': 'explicit-legacy-cannot-enable-admin', 'status': 'PASS'}]
    for mode in ('single_admin', 'local_demo', 'tls'):
        current = dict(env)
        files = ['compose.app.yml', 'compose.auth.yml']
        if mode == 'local_demo':
            current.update(AUTH_MODE='local_demo', AUTH_APP_ENVIRONMENT='local', AUTH_DEV_ADMIN_BYPASS='true')
        if mode == 'tls':
            current.update(AUTH_ORIGIN='https://127.0.0.1:18443', WEB_TLS_PORT='18443', AUTH_BIND_IP='127.0.0.1',
                AUTH_TLS_CERT_FILE=(scratch / 'auth-acceptance/tls/server.crt').as_posix(),
                AUTH_TLS_KEY_FILE=(scratch / 'auth-acceptance/tls/server.key').as_posix())
            files.append('compose.auth-tls.yml')
        env_file = profile / (mode + '.env')
        env_file.write_text('\n'.join(f'{key}={value}' for key, value in current.items()), encoding='utf-8')
        command = docker + ['compose', '--env-file', str(env_file)]
        for file in files:
            command += ['-f', str(root / 'deploy' / file)]
        raw = subprocess.check_output(command + ['--profile', 'workers', 'config', '--format', 'json'], text=True)
        result = subprocess.run(docker + ['run', '--rm', '--network', 'none', '-i', '--entrypoint', 'python',
            env['BACKEND_IMAGE'], '/opt/etl/auth_deployment.py'], input=raw, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        rows.append({'case': 'compose-' + mode, 'status': 'PASS'})
        if mode == 'local_demo':
            doc = json.loads(raw)
            doc['services']['frontend']['ports'][0]['host_ip'] = '0.0.0.0'
            denied = subprocess.run(docker + ['run', '--rm', '--network', 'none', '-i', '--entrypoint', 'python',
                env['BACKEND_IMAGE'], '/opt/etl/auth_deployment.py'], input=json.dumps(doc), text=True, capture_output=True)
            assert denied.returncode == 1 and 'AUTH_LOOPBACK_BIND_REQUIRED' in denied.stderr
            rows.append({'case': 'bypass-LAN-bind-rejected', 'status': 'PASS'})
    startup = docker + ['run', '--rm', '--network', 'none', '--read-only', '--tmpfs',
        '/var/lib/etl/auth:rw,uid=10001,gid=10001,mode=0700']
    for name in ('admin_password_hash', 'session_signing_key', 'auth_proxy_key'):
        startup += ['--mount', f'type=bind,source={private / name},target=/run/secrets/{name},readonly']
    settings = {'AUTH_MODE': 'single_admin', 'AUTH_ORIGIN': 'http://127.0.0.1:18088',
        'AUTH_DEPLOYMENT_PROFILE': 'localhost', 'ADMIN_LOGIN_EMAIL': 'admin@etl.test',
        'ADMIN_PASSWORD_HASH_FILE': '/run/secrets/admin_password_hash',
        'SESSION_SIGNING_KEY_FILE': '/run/secrets/session_signing_key', 'AUTH_PROXY_KEY_FILE': '/run/secrets/auth_proxy_key'}
    for key, value in settings.items():
        startup += ['-e', f'{key}={value}']
    result = subprocess.run(startup + ['--entrypoint', 'python', env['BACKEND_IMAGE'], '/opt/etl/runtime.py', 'check-auth'], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    rows.append({'case': 'runtime-check-auth-no-network-no-DB', 'status': 'PASS'})
    (scratch / 'auth-profile-evidence.json').write_text(json.dumps(rows, indent=2))
    print('PASS: effective Compose profiles and startup auth check; no databases contacted')


if __name__ == '__main__':
    main()
