"""Container entrypoint; never loads host .env or performs migration/bootstrap."""
import json
import os
import re
import socket
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

CONNECTION_KEYS = {'DATABASE_URL', 'SOURCE_DATABASE_URL', 'MONGO_URI', 'MONGO_DATABASE'}
WORKERS = {'run-master-sync-worker': 'master-sync', 'run-source-file-worker': 'source-file', 'run-ocr-worker': 'ocr'}
SOURCE_CA_FILE = Path('/run/secrets/source-database-ca.pem')


def validate_source_neon(values):
    """Remote SOURCE only; local service names cannot hide other remote databases."""
    source = urlsplit(values['SOURCE_DATABASE_URL'])
    query = parse_qs(source.query, keep_blank_values=True)
    if (source.scheme != 'postgresql+psycopg' or not source.hostname
            or not source.hostname.endswith('.neon.tech')):
        raise ValueError('SOURCE_NEON_HOST_REQUIRED')
    if not source.username or source.username in ('neondb_owner', 'postgres'):
        raise ValueError('SOURCE_PRIVILEGED_ROLE_FORBIDDEN')
    if (query.get('sslmode') != ['verify-full'] or query.get('channel_binding') != ['require']
            or query.get('sslrootcert') != [str(SOURCE_CA_FILE)]):
        raise ValueError('SOURCE_NEON_TLS_REQUIRED')
    if set(query) - {'sslmode', 'sslrootcert', 'channel_binding', 'connect_timeout', 'application_name'}:
        raise ValueError('SOURCE_NEON_CONNECTION_OVERRIDE_FORBIDDEN')
    if not SOURCE_CA_FILE.is_file():
        raise ValueError('SOURCE_NEON_CA_REQUIRED')
    master, mongo = urlsplit(values['DATABASE_URL']), urlsplit(values['MONGO_URI'])
    overrides = {'host', 'hostaddr', 'port', 'dbname', 'user', 'password', 'service', 'servicefile'}
    if (master.scheme != 'postgresql+psycopg' or master.hostname not in ('postgres', 'master-postgres')
            or overrides.intersection(parse_qs(master.query, keep_blank_values=True))):
        raise ValueError('SOURCE_NEON_MASTER_LOCAL_REQUIRED')
    if mongo.scheme != 'mongodb' or mongo.hostname != 'mongo' or ',' in mongo.netloc:
        raise ValueError('SOURCE_NEON_MONGO_LOCAL_REQUIRED')


class DatabaseSchemaNotReady(ValueError):
    def __init__(self, target, expected, actual):
        super().__init__('DATABASE_SCHEMA_NOT_READY')
        self.detail = {'database': target, 'expected_count': len(expected),
                       'actual_count': len(actual), 'missing_tables': sorted(expected - actual),
                       'unexpected_tables': sorted(actual - expected)}


def check_table_contract(cursor, target, expected):
    # information_schema.tables hides tables without grants. The SOURCE reader
    # intentionally reads only 11 of the 15 tables; inventory is not authorization.
    cursor.execute('SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=current_schema()')
    actual = {row[0] for row in cursor.fetchall()} - {'alembic_version', 'alembic_source_version'}
    if actual != expected:
        raise DatabaseSchemaNotReady(target, expected, actual)


def source_table_names(path=Path('/app/alembic_source/schema_v1.sql')):
    names = set(re.findall(r'CREATE TABLE ([a-z_]+)\s*\(', path.read_text(encoding='utf-8')))
    if len(names) != 15:
        raise ValueError('SOURCE_SCHEMA_CONTRACT_INVALID')
    return names


def load_connections():
    path = os.environ.get('ETL_CONNECTIONS_FILE')
    if not path:
        raise ValueError('CONNECTIONS_SECRET_REQUIRED')
    raw = Path(path).read_bytes()
    if len(raw) > 65536:
        raise ValueError('CONNECTIONS_SECRET_INVALID')
    values = json.loads(raw)
    if not isinstance(values, dict) or set(values) != CONNECTION_KEYS:
        raise ValueError('CONNECTIONS_SECRET_KEYS_INVALID')
    if any(not isinstance(value, str) or not value.strip() or 'CHANGE_ME' in value for value in values.values()):
        raise ValueError('CONNECTIONS_SECRET_VALUES_INVALID')
    if 'SOURCE_MIGRATION_DATABASE_URL' in os.environ:
        raise ValueError('SOURCE_MIGRATION_CREDENTIAL_FORBIDDEN')
    mode = os.environ.get('ETL_DATABASE_MODE', 'same-host')
    if mode not in ('same-host', 'remote', 'source-neon'):
        raise ValueError('DATABASE_MODE_INVALID')
    for key in ('DATABASE_URL', 'SOURCE_DATABASE_URL', 'MONGO_URI'):
        url = urlsplit(values[key])
        if not url.hostname or url.hostname in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('DATABASE_CONTAINER_HOST_INVALID')
        if mode == 'remote':
            query = parse_qs(url.query)
            if key != 'MONGO_URI' and query.get('sslmode') != ['verify-full']:
                raise ValueError('REMOTE_POSTGRES_TLS_REQUIRED')
            if key == 'MONGO_URI' and (query.get('tls') != ['true'] or any(k.lower().startswith(('tlsallowinvalid', 'tlsinsecure')) for k in query)):
                raise ValueError('REMOTE_MONGO_TLS_REQUIRED')
    if mode == 'source-neon':
        validate_source_neon(values)
    os.environ.update(values)


def verify_runtime(role):
    authenticated = role == 'api' and os.environ.get('AUTH_MODE', 'legacy_local') != 'legacy_local'
    if authenticated:
        from data_sync_etl.config import Settings
        from data_sync_etl.security.auth import create_auth_service, validate_mode
        if os.environ.get('AUTH_MODE') == 'master_users':
            validate_mode(Settings(_env_file=None))
            load_connections()
        create_auth_service(Settings(_env_file=None))
    else:
        if os.environ.get('ETL_ACCESS_MODE', 'local-locked') != 'local-locked':
            raise ValueError('BLOCKED_AUTH_PUBLIC')
        if os.environ.get('ADMIN_ENABLED', '').lower() != 'false' or os.environ.get('SEARCH_INTERNAL_DEV_ENABLED', '').lower() != 'false':
            raise ValueError('LOCAL_DEMO_GRANT_FORBIDDEN_IN_CONTAINER')
        if os.environ.get('APP_ENVIRONMENT') != 'production':
            raise ValueError('PRODUCTION_BOUNDARY_REQUIRED')
    if os.environ.get('SEARCH_INTERNAL_DEV_ENABLED', '').lower() != 'false':
        raise ValueError('LEGACY_SEARCH_GRANT_FORBIDDEN_IN_CONTAINER')
    load_connections()
    if role in ('api', 'ocr'):
        from model import verify
        verify(Path(os.environ['INDEX_EMBEDDING_MODEL_PATH']), Path('/opt/model-manifest.json'))
    if role == 'source-file' and any(os.environ.get(key, '').lower() != 'true' for key in ('SOURCE_REFERENCE_ENABLED', 'SOURCE_TEMPORARY_FILES_ENABLED')):
        raise ValueError('SOURCE_REFERENCE_POLICY_BLOCKED')


def health_worker():
    marker = Path(os.environ.get('ETL_WORKER_HEARTBEAT_FILE', '/run/etl/worker-heartbeat.json'))
    data = json.loads(marker.read_text(encoding='utf-8'))
    if data['role'] != os.environ['ETL_SERVICE_ROLE'] or time.time() - marker.stat().st_mtime > 450:
        raise ValueError('WORKER_HEARTBEAT_STALE')
    os.kill(int(data['pid']), 0)


def check_postgres_connection(connection, target, expected):
    # Match the SOURCE adapter's explicit readonly transaction. The role's
    # default may be read-write even when it has no write privileges.
    # Preflight never needs a writable transaction, including on Master.
    connection.read_only = True
    with connection.cursor() as cursor:
        cursor.execute('SELECT 1')
        assert cursor.fetchone() == (1,)
        check_table_contract(cursor, target, expected)
        if target == 'SOURCE':
            cursor.execute('SHOW transaction_read_only')
            if cursor.fetchone()[0] != 'on':
                raise ValueError('SOURCE_READ_ONLY_ROLE_REQUIRED')
            cursor.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')
            if any(cursor.fetchone()):
                raise ValueError('SOURCE_PRIVILEGED_ROLE_FORBIDDEN')
            for table in expected:
                cursor.execute("SELECT has_table_privilege(current_user,quote_ident(current_schema())||'.'||quote_ident(%s),'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')", (table,))
                if cursor.fetchone()[0]:
                    raise ValueError('SOURCE_WRITE_PRIVILEGES_FORBIDDEN')


def check_database():
    load_connections()
    import psycopg
    from pymongo import MongoClient

    from data_sync_etl.adapters.mongo.schema import check_schema
    from data_sync_etl.db.models import Base
    for key in ('DATABASE_URL', 'SOURCE_DATABASE_URL'):
        with psycopg.connect(os.environ[key].replace('postgresql+psycopg://', 'postgresql://'), connect_timeout=5) as connection:
            expected = set(Base.metadata.tables) if key == 'DATABASE_URL' else source_table_names()
            check_postgres_connection(connection, 'MASTER' if key == 'DATABASE_URL' else 'SOURCE', expected)
    with MongoClient(os.environ['MONGO_URI'], serverSelectionTimeoutMS=5000) as client:
        client.admin.command('ping')
        if not check_schema(client[os.environ['MONGO_DATABASE']],
                            dimension=int(os.environ.get('INDEX_EMBEDDING_DIMENSION', '384')),
                            historical_dimensions=json.loads(os.environ.get('INDEX_HISTORICAL_EMBEDDING_DIMENSIONS', '[]')))['schema_ready']:
            raise ValueError('MONGO_SCHEMA_NOT_READY')


def main():
    try:
        action = sys.argv[1] if len(sys.argv) > 1 else 'api'
        if action == 'health-api':
            with urlopen('http://127.0.0.1:8000/health', timeout=4) as response:
                assert response.status == 200
            return 0
        if action == 'health-worker':
            health_worker()
            return 0
        if action == 'check-db':
            check_database()
            print('{"status":"PASS","operation":"database_readonly_check"}')
            return 0
        if action == 'check-auth':
            from data_sync_etl.config import Settings
            from data_sync_etl.security.auth import create_auth_service, validate_mode
            if os.environ.get('AUTH_MODE') == 'master_users':
                validate_mode(Settings(_env_file=None))
                load_connections()
            create_auth_service(Settings(_env_file=None))
            print('{"status":"PASS","operation":"auth_configuration"}')
            return 0
        if action == 'api':
            verify_runtime('api')
            os.execvp('uvicorn', ['uvicorn', 'data_sync_etl.main:app', '--host', '0.0.0.0', '--port', '8000', '--no-proxy-headers'])
        if action in ('semantic-schema', 'semantic-index', 'semantic-status'):
            # Operator-only CLI inside the container, not a new HTTP admin route.
            # Runtime secrets still pass through the existing network/TLS checks.
            load_connections()
            os.execvp('python', ['python', '-m', 'data_sync_etl.cli.main', *sys.argv[1:]])
        if action == 'audit-worker':
            verify_runtime('reconciliation')
            os.environ['RECONCILIATION_WORKER_HEARTBEAT_FILE'] = '/run/etl/worker-heartbeat.json'
            os.execvp('python', ['python', '-m', 'data_sync_etl.workers.reconciliation'])
        if action == 'worker' and len(sys.argv) == 3 and sys.argv[2] in WORKERS:
            command = sys.argv[2]
            role = WORKERS[command]
            verify_runtime(role)
            os.environ['ETL_SERVICE_ROLE'] = role
            os.environ['ETL_WORKER_HEARTBEAT_FILE'] = '/run/etl/worker-heartbeat.json'
            worker_id = f'{role}-{socket.gethostname()}'[:100]
            os.execvp('etl', ['etl', command, '--worker-id', worker_id])
        raise ValueError('CONTAINER_ACTION_NOT_ALLOWED')
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and str(error).replace('_', '').isalnum() else 'CONTAINER_OPERATION_BLOCKED'
        payload = {'status': 'BLOCKED', 'code': code}
        if isinstance(error, DatabaseSchemaNotReady):
            payload['detail'] = error.detail
        print(json.dumps(payload), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
