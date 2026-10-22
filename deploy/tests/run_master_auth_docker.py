"""Isolated two-realm browser acceptance; never connects to a business database."""

import json
import os
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import urlopen

from argon2 import PasswordHasher


def main():
    root = Path(__file__).resolve().parents[2]
    project = 'etl-master-login-qa-' + secrets.token_hex(4)
    output = root / '.test-tmp/database-auth' / project
    output.mkdir(parents=True)
    private = Path(tempfile.mkdtemp(prefix=project))
    password = secrets.token_urlsafe(24)
    values = {'qa_password': password,
        'admin_password_hash': PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1).hash(password),
        'session_signing_key': secrets.token_hex(32), 'auth_proxy_key': secrets.token_hex(32)}
    for name, value in values.items():
        path = private / name
        path.write_text(value, encoding='ascii')
        path.chmod(0o640 if name == 'auth_proxy_key' else 0o600)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    origin = f'http://127.0.0.1:{port}'
    document = f'''name: {project}
services:
  api:
    image: etl-master-auth-qa:local
    entrypoint: ["/usr/bin/tini", "-g", "--", "uvicorn", "deploy.tests.master_auth_test_server:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
    user: "10001:10001"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    tmpfs: ["/run/etl:rw,uid=10001,gid=10001,mode=0700", "/tmp:rw,uid=10001,gid=10001,mode=0700"]
    environment:
      AUTH_MODE: master_users
      AUTH_ORIGIN: "{origin}"
      AUTH_DEPLOYMENT_PROFILE: localhost
      AUTH_PUBLIC_ENABLED: "false"
      AUTH_DEV_ADMIN_BYPASS: "false"
      ADMIN_ENABLED: "true"
      ADMIN_LOGIN_EMAIL: admin@etl.test
      ADMIN_PASSWORD_HASH_FILE: /run/secrets/admin_password_hash
      SESSION_SIGNING_KEY_FILE: /run/secrets/session_signing_key
      AUTH_PROXY_KEY_FILE: /run/secrets/auth_proxy_key
      AUTH_STATE_PATH: /run/etl/auth-sessions.sqlite3
    secrets: [qa_password, admin_password_hash, session_signing_key, auth_proxy_key]
  frontend:
    image: etl-portable-frontend:master-users-local
    user: "101:101"
    group_add: ["10001"]
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    tmpfs: ["/tmp:rw,uid=101,gid=101,mode=0700"]
    ports: ["127.0.0.1:{port}:8080"]
    environment: {{AUTH_MODE: master_users, AUTH_ORIGIN: "{origin}", AUTH_TLS: "false"}}
    secrets: [auth_proxy_key]
    depends_on: [api]
secrets:
'''
    for name in values:
        document += f'  {name}: {{file: "{(private / name).as_posix()}"}}\n'
    compose_file = output / 'compose.qa.yml'
    compose_file.write_text(document, encoding='utf-8')
    command = ['docker', 'compose', '-f', str(compose_file)]
    subprocess.run(command + ['up', '-d', '--no-build'], check=True)
    try:
        for _ in range(45):
            try:
                with urlopen(origin + '/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('ISOLATED_MASTER_LOGIN_NOT_READY')
        env = {**os.environ, 'ETL_MASTER_AUTH_QA_ORIGIN': origin, 'ETL_MASTER_AUTH_QA_PASSWORD': password,
               'ETL_MASTER_AUTH_QA_API': project + '-api-1',
               'ETL_MASTER_AUTH_QA_OUTPUT': str(output)}
        result = subprocess.run(['node', '--test', 'deploy/tests/master_auth_browser.test.mjs'], cwd=root,
            env=env, capture_output=True, text=True, encoding='utf-8')
        (output / 'browser.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        print(result.stdout)
        print(json.dumps({'exit': result.returncode, 'evidence': str(output)}))
        return result.returncode
    finally:
        subprocess.run(command + ['stop'], check=True)


if __name__ == '__main__':
    raise SystemExit(main())
