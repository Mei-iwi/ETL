import importlib.util
import json
import os
import signal
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        seen = set()
        for key, _ in node.value:
            if key.tag.endswith(':merge'):
                continue
            value = self.construct_object(key, deep=deep)
            if value in seen:
                raise ValueError('Duplicate YAML key')
            seen.add(value)
        return super().construct_mapping(node, deep=deep)


def load_runtime(name):
    path = ROOT / 'deploy/runtime' / (name + '.py')
    spec = importlib.util.spec_from_file_location('portable_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_compose_no_bypass_or_startup_migration():
    data = yaml.load((ROOT / 'deploy/compose.app.yml').read_text(encoding='utf-8'), Loader=UniqueLoader)
    services = data['services']
    assert {'api', 'frontend', 'master-sync-worker', 'source-file-worker', 'ocr-worker'} <= services.keys()
    for name, service in services.items():
        assert not service.get('privileged')
        assert not any('docker.sock' in str(mount) for mount in service.get('volumes', []))
        assert all(str(port).startswith('127.0.0.1:') for port in service.get('ports', []))
        if name == 'frontend':
            continue
        assert service['environment']['ADMIN_ENABLED'] == 'false'
        assert service['environment']['SEARCH_INTERNAL_DEV_ENABLED'] == 'false'
        assert 'SOURCE_MIGRATION_DATABASE_URL' not in service['environment']
        assert service['read_only'] is True and service['user'] == '10001:10001'
        assert service['image'] == services['api']['image']
        assert all(str(mount).startswith('/') for mount in service.get('tmpfs', []))
    for name in ('master-sync-worker', 'source-file-worker'):
        assert not any(m.get('target') == '/opt/models/minilm' for m in services[name].get('volumes', []) if isinstance(m, dict))
    assert all('migrat' not in name for name in services)


def test_secret_allowlist_and_remote_tls_fail_closed(tmp_path, monkeypatch):
    runtime = load_runtime('runtime')
    secret = tmp_path / 'connections.json'
    values = dict(DATABASE_URL='postgresql+psycopg://u:p@master:5432/db', SOURCE_DATABASE_URL='postgresql+psycopg://u:p@source:5432/db', MONGO_URI='mongodb://mongo/db', MONGO_DATABASE='db')
    secret.write_text(json.dumps(values))
    monkeypatch.setenv('ETL_CONNECTIONS_FILE', str(secret))
    monkeypatch.delenv('SOURCE_MIGRATION_DATABASE_URL', raising=False)
    monkeypatch.setenv('ETL_DATABASE_MODE', 'remote')
    with pytest.raises(ValueError, match='TLS_REQUIRED'):
        runtime.load_connections()
    monkeypatch.setenv('ETL_DATABASE_MODE', 'same-host')
    values['ADMIN_ENABLED'] = 'true'
    secret.write_text(json.dumps(values))
    with pytest.raises(ValueError, match='KEYS_INVALID'):
        runtime.load_connections()


def test_public_mode_never_loads_database_secrets(monkeypatch):
    runtime = load_runtime('runtime')
    monkeypatch.setenv('ETL_ACCESS_MODE', 'network')
    monkeypatch.setattr(runtime, 'load_connections', lambda: pytest.fail('Auth gate must run before DB'))
    with pytest.raises(ValueError, match='BLOCKED_AUTH_PUBLIC'):
        runtime.verify_runtime('api')


@pytest.fixture
def source_neon_connections(tmp_path, monkeypatch):
    runtime = load_runtime('runtime')
    ca = tmp_path / 'source-ca.pem'
    ca.write_text('isolated fixture; TLS negotiation is not performed by this test')
    monkeypatch.setattr(runtime, 'SOURCE_CA_FILE', ca)
    secret = tmp_path / 'connections.json'
    values = {'DATABASE_URL': 'postgresql+psycopg://runtime:fixture@postgres:5432/etl',
              'SOURCE_DATABASE_URL': 'postgresql+psycopg://reader:fixture@ep-example.us-east-2.aws.neon.tech/source'
                  f'?sslmode=verify-full&channel_binding=require&sslrootcert={ca.as_posix()}',
              'MONGO_URI': 'mongodb://mongo:27017', 'MONGO_DATABASE': 'etl_content'}
    monkeypatch.setenv('ETL_DATABASE_MODE', 'source-neon')
    monkeypatch.setenv('ETL_CONNECTIONS_FILE', str(secret))
    monkeypatch.delenv('SOURCE_MIGRATION_DATABASE_URL', raising=False)
    # URI uses a normalized POSIX path on Windows as well as Linux.
    monkeypatch.setattr(runtime, 'SOURCE_CA_FILE', Path(ca.as_posix()))
    return runtime, secret, values, ca


def test_neon_source_with_local_master_mongo(source_neon_connections):
    runtime, secret, values, _ = source_neon_connections
    # Windows Path string uses backslashes; URI must match the declared trust path.
    from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
    url = urlsplit(values['SOURCE_DATABASE_URL'])
    query = parse_qs(url.query)
    query['sslrootcert'] = [str(runtime.SOURCE_CA_FILE)]
    values['SOURCE_DATABASE_URL'] = urlunsplit(url._replace(query=urlencode(query, doseq=True)))
    secret.write_text(json.dumps(values))
    runtime.load_connections()
    assert os.environ['SOURCE_DATABASE_URL'] == values['SOURCE_DATABASE_URL']
    assert os.environ['DATABASE_URL'] == values['DATABASE_URL']
    assert os.environ['MONGO_URI'] == values['MONGO_URI']


@pytest.mark.parametrize(('key', 'replacement', 'error'), [
    ('source_ssl', 'require', 'SOURCE_NEON_TLS_REQUIRED'),
    ('source_ssl', 'disable', 'SOURCE_NEON_TLS_REQUIRED'),
    ('channel_binding', 'prefer', 'SOURCE_NEON_TLS_REQUIRED'),
    ('sslrootcert', '/tmp/untrusted.pem', 'SOURCE_NEON_TLS_REQUIRED'),
    ('hostaddr', '203.0.113.1', 'SOURCE_NEON_CONNECTION_OVERRIDE_FORBIDDEN'),
    ('DATABASE_URL', 'postgresql+psycopg://u:p@external.example/db', 'SOURCE_NEON_MASTER_LOCAL_REQUIRED'),
    ('DATABASE_URL', 'postgresql+psycopg://u:p@postgres/db?hostaddr=203.0.113.1', 'SOURCE_NEON_MASTER_LOCAL_REQUIRED'),
    ('MONGO_URI', 'mongodb://external.example/db', 'SOURCE_NEON_MONGO_LOCAL_REQUIRED'),
    ('MONGO_URI', 'mongodb://mongo:27017,external.example:27017/db', 'SOURCE_NEON_MONGO_LOCAL_REQUIRED'),
    ('source_host', 'not-neon.example', 'SOURCE_NEON_HOST_REQUIRED'),
    ('source_role', 'neondb_owner', 'SOURCE_PRIVILEGED_ROLE_FORBIDDEN'),
    ('missing_ca', '', 'SOURCE_NEON_CA_REQUIRED'),
])
def test_neon_source_mode_rejects_unsafe_connections(source_neon_connections, key, replacement, error):
    from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
    runtime, secret, values, ca = source_neon_connections
    url = urlsplit(values['SOURCE_DATABASE_URL'])
    query = parse_qs(url.query)
    query['sslrootcert'] = [str(runtime.SOURCE_CA_FILE)]
    if key in ('source_ssl', 'channel_binding', 'sslrootcert', 'hostaddr'):
        query['sslmode' if key == 'source_ssl' else key] = [replacement]
    elif key == 'source_host':
        url = url._replace(netloc='reader:fixture@' + replacement)
    elif key == 'source_role':
        url = url._replace(netloc=replacement + ':fixture@' + url.hostname)
    elif key == 'missing_ca':
        ca.unlink()  # Isolated fixture, never a deployed certificate.
    else:
        values[key] = replacement
    values['SOURCE_DATABASE_URL'] = urlunsplit(url._replace(query=urlencode(query, doseq=True)))
    secret.write_text(json.dumps(values))
    with pytest.raises(ValueError, match=error):
        runtime.load_connections()


def test_unknown_database_mode_is_rejected(source_neon_connections, monkeypatch):
    runtime, secret, values, _ = source_neon_connections
    secret.write_text(json.dumps(values))
    monkeypatch.setenv('ETL_DATABASE_MODE', 'typo')
    with pytest.raises(ValueError, match='DATABASE_MODE_INVALID'):
        runtime.load_connections()


def test_neon_overlay_mounts_only_source_ca_for_all_backend_services():
    data = yaml.load((ROOT / 'deploy/compose.source-neon.yml').read_text(encoding='utf-8'), Loader=UniqueLoader)
    assert set(data['services']) == {'api', 'master-sync-worker', 'source-file-worker', 'ocr-worker', 'preflight'}
    for service in data['services'].values():
        assert service['environment'] == {'ETL_DATABASE_MODE': 'source-neon'}
        assert service['secrets'] == [{'source': 'source-database-ca', 'target': 'source-database-ca.pem'}]


def test_source_preflight_uses_all_15_tables_not_the_10_metadata_streams():
    runtime = load_runtime('runtime')
    names = runtime.source_table_names(ROOT / 'alembic_source/schema_v1.sql')
    assert len(names) == 15
    assert {'id_counters', 'resource_versions', 'content_units', 'ocr_jobs', 'ocr_page_tasks'} <= names


def test_table_inventory_uses_catalog_independent_of_source_reader_grants():
    runtime = load_runtime('runtime')
    names = runtime.source_table_names(ROOT / 'alembic_source/schema_v1.sql')

    class Cursor:
        def execute(self, sql):
            assert 'pg_catalog.pg_tables' in sql
            assert 'information_schema' not in sql

        def fetchall(self):
            return [(name,) for name in names | {'alembic_source_version'}]

    runtime.check_table_contract(Cursor(), 'SOURCE', names)


def test_schema_mismatch_reports_target_and_diff_without_connection_secret(monkeypatch, capsys):
    runtime = load_runtime('runtime')
    monkeypatch.setattr('sys.argv', ['runtime.py', 'check-db'])

    def fail():
        raise runtime.DatabaseSchemaNotReady('MASTER', {'one', 'two'}, {'one', 'extra'})

    monkeypatch.setattr(runtime, 'check_database', fail)
    assert runtime.main() == 1
    payload = json.loads(capsys.readouterr().err)
    assert payload == {'status': 'BLOCKED', 'code': 'DATABASE_SCHEMA_NOT_READY', 'detail': {
        'database': 'MASTER', 'expected_count': 2, 'actual_count': 2,
        'missing_tables': ['two'], 'unexpected_tables': ['extra']}}


@pytest.mark.parametrize(('elevated', 'write_grants', 'server_readonly', 'error'), [
    (False, False, 'on', None),
    (True, False, 'on', 'SOURCE_PRIVILEGED_ROLE_FORBIDDEN'),
    (False, True, 'on', 'SOURCE_WRITE_PRIVILEGES_FORBIDDEN'),
    (False, False, 'off', 'SOURCE_READ_ONLY_ROLE_REQUIRED'),
])
def test_readonly_session_preserves_source_privilege_guards(elevated, write_grants, server_readonly, error):
    runtime = load_runtime('runtime')

    class Connection:
        read_only = False

        def cursor(self):
            return Cursor()

    connection = Connection()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execute(self, sql, params=None):
            assert connection.read_only is True  # before the first statement
            self.sql = sql

        def fetchall(self):
            return [('one',)]

        def fetchone(self):
            if self.sql == 'SELECT 1':
                return (1,)
            if self.sql.startswith('SHOW'):
                return (server_readonly,)
            if 'FROM pg_roles' in self.sql:
                return (elevated, False, False)
            assert 'has_table_privilege' in self.sql
            return (write_grants,)

    if error:
        with pytest.raises(ValueError, match=error):
            runtime.check_postgres_connection(connection, 'SOURCE', {'one'})
    else:
        runtime.check_postgres_connection(connection, 'SOURCE', {'one'})


def test_ops_denies_before_opening_owner_secret(monkeypatch, capsys):
    ops = load_runtime('ops')
    monkeypatch.delenv('ETL_MAINTENANCE_APPROVAL', raising=False)
    monkeypatch.setattr('sys.argv', ['ops.py', 'source-migrate'])
    monkeypatch.setattr(Path, 'read_text', lambda *a, **kw: pytest.fail('Unapproved owner secret read'))
    assert ops.main() == 2
    assert 'BLOCKED' in capsys.readouterr().err


def test_model_rejects_wrong_contract_before_loading(tmp_path):
    model = load_runtime('model')
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'model_id': model.MODEL_ID, 'revision': 'wrong', 'dimension': 384, 'files': []}))
    with pytest.raises(ValueError, match='CONTRACT_MISMATCH'):
        model.verify(tmp_path, manifest)


@pytest.mark.parametrize('dimension', [384, 1024])
def test_model_manifest_configured_contract_and_tamper_detection(tmp_path, monkeypatch, dimension):
    model = load_runtime('model')
    revision = 'a' * 40
    monkeypatch.setenv('INDEX_EMBEDDING_MODEL_ID', 'BAAI/bge-m3' if dimension == 1024 else model.MODEL_ID)
    monkeypatch.setenv('INDEX_EMBEDDING_MODEL_VERSION', revision)
    monkeypatch.setenv('INDEX_EMBEDDING_DIMENSION', str(dimension))
    (tmp_path / 'etl-model-revision.txt').write_text(revision)
    (tmp_path / 'modules.json').write_text('[{"path":""}]')
    weights = tmp_path / 'pytorch_model.bin'
    weights.write_bytes(b'fixture only, never loaded')
    document = tmp_path / 'etl-model-manifest.json'
    document.write_text(json.dumps(model.manifest(tmp_path)))
    assert model.verify(tmp_path, document)['dimension'] == dimension
    data = json.loads(document.read_text())
    data['files'].reverse()
    document.write_text(json.dumps(data))
    assert model.verify(tmp_path, document)['dimension'] == dimension
    with pytest.raises(ValueError, match='CONTRACT_MISMATCH'):
        model.verify(tmp_path, document, dimension=1024 if dimension == 384 else 384)
    weights.write_bytes(b'tampered fixture')
    with pytest.raises(ValueError, match='CHECKSUM_MISMATCH'):
        model.verify(tmp_path, document)


@pytest.mark.parametrize(('revision', 'dimension'), [('main', 1024), ('a' * 40, 0), ('a' * 40, 4097)])
def test_model_contract_requires_pinned_revision_and_bounded_dimension(revision, dimension):
    model = load_runtime('model')
    with pytest.raises(ValueError, match='CONTRACT_INVALID'):
        model.contract('BAAI/bge-m3', revision, dimension)


def test_model_file_order_is_identical_on_windows_and_linux(tmp_path):
    model = load_runtime('model')
    for name in ['config.json', 'README.md', 'a.json', 'Z.json']:
        (tmp_path / name).write_text('fixture')
    assert [path.name for path in model.model_files(tmp_path)] == ['README.md', 'Z.json', 'a.json', 'config.json']


def test_sigterm_stops_claims_and_restores_handlers(tmp_path, monkeypatch):
    from data_sync_etl.workers.lifecycle import tick, worker_lifecycle
    marker = tmp_path / 'heartbeat.json'
    monkeypatch.setenv('ETL_WORKER_HEARTBEAT_FILE', str(marker))
    original = signal.getsignal(signal.SIGTERM)
    with worker_lifecycle() as stop:
        tick()
        assert json.loads(marker.read_text())['pid'] == os.getpid()
        signal.raise_signal(signal.SIGTERM)
        assert stop.is_set()
    assert not marker.exists()
    assert signal.getsignal(signal.SIGTERM) == original
