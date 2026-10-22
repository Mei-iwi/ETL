"""Own QA resources only; test secrets remain outside the repository."""

import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from argon2 import PasswordHasher
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from data_sync_etl.security.auth_database import AuthUserRepository


def main():
    root = Path(__file__).resolve().parents[2]
    output = root / '.test-tmp/database-auth'
    state_file = output / 'qa-resources.json'
    state = json.loads(state_file.read_text(encoding='utf-8'))
    name, network = state['container'], state['network']
    meta = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
    assert meta['Config']['Labels'].get('etl.auth.test.owner') == 'database-auth-20261022'
    private = Path(state['private_directory'])
    owner = make_url((private / 'owner-url').read_text(encoding='ascii'))
    assert owner.host == '127.0.0.1' and owner.database == 'etl_auth_test'
    port = int(meta['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'])
    base_owner = owner.set(port=port)
    # Each browser run owns a fresh Auth DB; keep all previous accounts/evidence.
    database = 'etl_auth_browser_' + secrets.token_hex(6)
    maintenance = create_engine(base_owner, isolation_level='AUTOCOMMIT', hide_parameters=True)
    with maintenance.connect() as connection:
        with connection.connection.driver_connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database)))
    maintenance.dispose()
    owner = base_owner.set(database=database)
    engine = create_engine(owner, hide_parameters=True)
    repo = AuthUserRepository(engine)
    repo.migrate()
    username = 'auth_runtime_' + secrets.token_hex(6)
    db_password = secrets.token_urlsafe(32)
    with engine.begin() as connection:
        with connection.connection.driver_connection.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE').format(
                sql.Identifier(username), sql.Literal(db_password)))
            cursor.execute(sql.SQL('GRANT CONNECT ON DATABASE {} TO {}').format(
                sql.Identifier(database), sql.Identifier(username)))
            for template in ['GRANT USAGE ON SCHEMA public TO {}',
                             'GRANT SELECT,INSERT,UPDATE,DELETE ON auth_users,auth_user_roles TO {}',
                             'GRANT SELECT ON auth_roles,auth_schema_versions TO {}']:
                cursor.execute(sql.SQL(template).format(sql.Identifier(username)))
    runtime = owner.set(username=username, password=db_password, host=name, port=5432)
    values = {'auth_database_url': runtime.render_as_string(hide_password=False),
              'session_signing_key': secrets.token_hex(32), 'auth_proxy_key': secrets.token_hex(32)}
    run_private = private / ('browser-' + secrets.token_hex(6))
    run_private.mkdir(mode=0o700)
    for filename, value in values.items():
        path = run_private / filename
        path.write_text(value, encoding='ascii')
        path.chmod(0o640 if filename == 'auth_proxy_key' else 0o600)
    password, user_password, new_password = (secrets.token_urlsafe(24) for _ in range(3))
    repo.create('admin@etl.test', 'Administrator', PasswordHasher().hash(password), ['admin'], bootstrap=True)
    engine.dispose()
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        web_port = probe.getsockname()[1]
    origin = f'http://127.0.0.1:{web_port}'
    project = 'etl-db-auth-qa-' + secrets.token_hex(4)
    document = f'''name: {project}
services:
  api:
    image: etl-db-auth-qa:final
    entrypoint: ["/usr/bin/tini", "-g", "--", "uvicorn", "deploy.tests.auth_test_server:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
    user: "10001:10001"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    tmpfs: ["/run/etl:rw,uid=10001,gid=10001,mode=0700", "/tmp:rw,uid=10001,gid=10001,mode=0700"]
    environment:
      AUTH_MODE: db_users
      APP_ENVIRONMENT: production
      AUTH_DEPLOYMENT_PROFILE: localhost
      AUTH_DEV_ADMIN_BYPASS: "false"
      AUTH_PUBLIC_ENABLED: "false"
      AUTH_COOKIE_SECURE: "false"
      AUTH_ORIGIN: "{origin}"
      ADMIN_ENABLED: "true"
      AUTH_DATABASE_URL_FILE: /run/secrets/auth_database_url
      SESSION_SIGNING_KEY_FILE: /run/secrets/session_signing_key
      AUTH_PROXY_KEY_FILE: /run/secrets/auth_proxy_key
      AUTH_STATE_PATH: /var/lib/etl/auth/sessions.sqlite3
    secrets: [auth_database_url, session_signing_key, auth_proxy_key]
    volumes: [auth-sessions:/var/lib/etl/auth]
    networks: [qa]
  frontend:
    image: etl-db-auth-frontend:qa
    group_add: ["10001"]
    user: "101:101"
    read_only: true
    cap_drop: [ALL]
    security_opt: [no-new-privileges:true]
    tmpfs: ["/tmp:rw,uid=101,gid=101,mode=0700"]
    ports: ["127.0.0.1:{web_port}:8080"]
    environment: {{AUTH_MODE: db_users, AUTH_ORIGIN: "{origin}", AUTH_TLS: "false"}}
    secrets: [auth_proxy_key]
    networks: [qa]
    depends_on: [api]
networks:
  qa: {{external: true, name: {network}}}
volumes:
  auth-sessions:
secrets:
'''
    for filename in values:
        document += f'  {filename}: {{file: "{(run_private / filename).as_posix()}"}}\n'
    compose_file = output / f'compose.{project}.yml'
    compose_file.write_text(document, encoding='utf-8')
    compose = ['docker', 'compose', '-f', str(compose_file)]
    subprocess.run(compose + ['up', '-d', '--no-build'], check=True, cwd=root)
    try:
        for _ in range(90):
            try:
                with urlopen(origin + '/health', timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError('ISOLATED_AUTH_API_NOT_READY')
        env = {**os.environ, 'ETL_DB_AUTH_QA_ORIGIN': origin, 'ETL_DB_AUTH_QA_PASSWORD': password,
               'ETL_DB_AUTH_QA_USER_PASSWORD': user_password, 'ETL_DB_AUTH_QA_NEW_PASSWORD': new_password,
               'ETL_DB_AUTH_QA_OUTPUT': str(output / project),
               'ETL_DB_AUTH_QA_API': project + '-api-1', 'ETL_DB_AUTH_QA_POSTGRES': name}
        result = subprocess.run(['node', '--test', 'deploy/tests/database_auth_browser.test.mjs'], env=env,
                                cwd=root, capture_output=True, text=True, encoding='utf-8')
        (output / f'{project}-browser.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        print(result.stdout, flush=True)
        if result.returncode:
            return result.returncode
        print('PASS: Docker db_users with restricted PostgreSQL role and real React browser flow.')
        return 0
    finally:
        subprocess.run(compose + ['stop'], check=True, cwd=root)
        # Refresh test-only host URL if Docker assigned another ephemeral port.
        meta = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
        state['port'] = int(meta['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'])
        (private / 'owner-url').write_text(base_owner.set(port=state['port']).render_as_string(hide_password=False), encoding='ascii')
        state_file.write_text(json.dumps(state, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
