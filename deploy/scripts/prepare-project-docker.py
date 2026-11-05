"""Prepare private Docker config from the existing authorized project .env.

Run with the project virtualenv at the repository root. This only writes
configuration/secret files; it never migrates databases or changes contracts.
Existing database network is etl-master-only_default (MASTER-only Compose).
"""
import json
import shutil
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml
from dotenv import dotenv_values

from data_sync_etl.config import Settings

root = Path(__file__).resolve().parents[2]
raw = {k: v for k, v in dotenv_values(root / '.env').items() if v is not None}
settings = Settings()
private = root / 'ETL_Secrets/docker'
env_secrets_dir = private / 'runtime-env-secrets'
private.mkdir(parents=True, exist_ok=True)
if (root / 'docker.env').exists() and not (private / 'docker.env.before.backup').exists():
    shutil.copy2(root / 'docker.env', private / 'docker.env.before.backup')

def host(value):
    p = Path(value)
    return (p if p.is_absolute() else root / p).resolve()

def linux_url(value):
    url = urlsplit(value)
    query = dict(parse_qsl(url.query))
    if 'sslrootcert' in query:
        query['sslrootcert'] = '/run/secrets/source-database-ca.pem'
    return urlunsplit(url._replace(query=urlencode(query)))

mongo_url = urlsplit(settings.mongo_uri)
native_mongo = (mongo_url.scheme == 'mongodb' and mongo_url.hostname in ('127.0.0.1', 'localhost')
                and mongo_url.port == 27017)
container_mongo_uri = (urlunsplit(mongo_url._replace(netloc='host.docker.internal:27017'))
                       if native_mongo else settings.mongo_uri)
if native_mongo and (mongo_url.username or mongo_url.password or mongo_url.query):
    raise ValueError('NATIVE_MONGO_CONNECTION_OPTIONS_UNSUPPORTED')
connections = {
    'DATABASE_URL': str(settings.database_url).replace('@127.0.0.1:5434/', '@master-postgres:5432/'),
    'SOURCE_DATABASE_URL': linux_url(str(settings.source_database_url)),
    'MONGO_URI': container_mongo_uri,
    'MONGO_DATABASE': settings.mongo_database,
}
assert '@master-postgres:5432/' in connections['DATABASE_URL']
assert connections['MONGO_DATABASE'] == 'etl_content'
# The owner opted into env-backed Compose secrets. Keep secret mounts and their
# runtime validators, without requiring separately maintained credential files.
private_values = dict(connections)
source_query = dict(parse_qsl(urlsplit(str(settings.source_database_url)).query))
ca = host(source_query['sslrootcert'])
assert ca.is_file()

# Emit non-secret settings into service environment; credentials remain scoped
# Compose secrets whose values are sourced from the private docker.env.
env = {}
for key, value in raw.items():
    if key.lower() not in Settings.model_fields:
        continue
    if any(word in key for word in ('PASSWORD', 'SIGNING_KEY', 'PROXY_KEY', 'ACCESS_KEY', 'SECRET_KEY')):
        continue
    if key.endswith('_CREDENTIALS_JSON'):
        continue
    if key.endswith(('_URL', '_URI', '_FILE', '_PATH', '_ROOT')):
        continue
    env[key] = value
env.update({
    'APP_ENVIRONMENT': 'production', 'AUTH_APP_ENVIRONMENT': 'production',
    'SEARCH_INTERNAL_DEV_ENABLED': 'false', 'AUTH_DEV_ADMIN_BYPASS': 'false',
    'AUTH_ORIGIN': 'http://127.0.0.1:8080', 'AUTH_DEPLOYMENT_PROFILE': 'localhost',
    'AUTH_COOKIE_SECURE': 'false', 'AUTH_STATE_PATH': '/var/lib/etl/auth/sessions.sqlite3',
    'STORAGE_ROOT': '/var/lib/etl/local', 'SOURCE_FILE_STORAGE_ROOT': '/mnt/source',
    'SOURCE_TEMP_ROOT': '/run/etl/source', 'TESSERACT_PATH': '/usr/bin/tesseract',
    'OFFICE_PDF_EXECUTABLE': '/usr/bin/libreoffice', 'FFMPEG_PATH': '/usr/bin/ffmpeg',
    'FFPROBE_PATH': '/usr/bin/ffprobe', 'INDEX_EMBEDDING_MODEL_PATH': '/opt/models/minilm',
    'WHISPER_MODEL_PATH': '/opt/models/whisper', 'TRANSLATION_MODEL_ROOT': '/opt/models/argos',
})
# Keep the exact approved host endpoints and contracts.
for key in raw:
    if key.endswith('_ENDPOINT_URL'):
        env[key] = raw[key]
secrets = {}
mounts = []


def config_secret(key, *file_keys):
    value = raw.get(key)
    if value:
        return value
    for file_key in file_keys:
        if raw.get(file_key):
            try:
                return host(raw[file_key]).read_text(encoding='utf-8').strip()
            except (OSError, UnicodeError):
                raise ValueError('PRIVATE_CONFIG_SECRET_UNREADABLE') from None
    return ''


def mount_env_secret(name, key, value):
    if not value:
        raise ValueError('PRIVATE_CONFIG_SECRET_REQUIRED')
    private_values[key] = value
    secrets[name] = {'file': (env_secrets_dir / (key.lower() + '.secret')).as_posix()}
    mounts.append({'source': name, 'target': name})
    return '/run/secrets/' + name

def mount_secret(name, file):
    p = host(file)
    assert p.is_file(), f'Missing required secret file: {name}'
    secrets[name] = {'file': p.as_posix()}
    mounts.append({'source': name, 'target': name})
    return '/run/secrets/' + name

for key in ['SOURCE_OCR_CONTRACT_FILE', 'SOURCE_WRITE_CONTRACT_FILE',
            'SOURCE_VERSION_WRITE_CONTRACT_FILE', 'SOURCE_STORAGE_WRITE_CONTRACT_FILE']:
    if raw.get(key):
        env[key] = mount_secret('project_' + key.lower(), raw[key])
if 'SOURCE_WRITE_CONTRACT_FILE' in env:
    env['SOURCE_WRITE_CONTRACT_SECRET_FILE'] = env['SOURCE_WRITE_CONTRACT_FILE']

credential_keys = {
    'SOURCE_WRITE_DATABASE_URL': 'SOURCE_WRITE_DATABASE_URL_SECRET_FILE',
    'ETL_FILE_STORAGE_ACCESS_KEY_ID': 'ETL_FILE_STORAGE_ACCESS_KEY_ID_FILE',
    'ETL_FILE_STORAGE_SECRET_ACCESS_KEY': 'ETL_FILE_STORAGE_SECRET_ACCESS_KEY_FILE',
    'SOURCE_STORAGE_WRITE_ACCESS_KEY_ID': 'SOURCE_STORAGE_WRITE_ACCESS_KEY_ID_FILE',
    'SOURCE_STORAGE_WRITE_SECRET_ACCESS_KEY': 'SOURCE_STORAGE_WRITE_SECRET_ACCESS_KEY_FILE',
}
for key, target in credential_keys.items():
    value = config_secret(key, target, key + '_FILE')
    if not value:
        continue
    if key == 'SOURCE_WRITE_DATABASE_URL':
        value = linux_url(value)
    env[target] = mount_env_secret('project_' + key.lower(), key, value)
env['SOURCE_WRITE_DATABASE_URL_FILE'] = env['SOURCE_WRITE_DATABASE_URL_SECRET_FILE']
env['SOURCE_FILE_STORAGE_ACCESS_KEY_ID_FILE'] = env['ETL_FILE_STORAGE_ACCESS_KEY_ID_FILE']
env['SOURCE_FILE_STORAGE_SECRET_ACCESS_KEY_FILE'] = env['ETL_FILE_STORAGE_SECRET_ACCESS_KEY_FILE']
env['SOURCE_DRIVE_CREDENTIALS_FILE'] = '/run/secrets/drive.json'
if raw.get('SOURCE_DRIVE_ENABLED') == 'true':
    private_values['SOURCE_DRIVE_CREDENTIALS_JSON'] = config_secret(
        'SOURCE_DRIVE_CREDENTIALS_JSON', 'SOURCE_DRIVE_CREDENTIALS_FILE')
    mounts.append({'source': 'drive', 'target': 'drive.json'})

source_dir = root / 'storage/source-fixtures-disabled'
source_dir.mkdir(parents=True, exist_ok=True)
config = dict(env)
# Match the exact hosts generated by S3ObjectStorage._target(); no signed URL,
# credentials or wildcard enters the frontend policy.
viewer_origins = set()
for prefix in ['source_file_storage', 'etl_file_storage', 'source_storage_write']:
    endpoint = getattr(settings, prefix + '_endpoint_url', None)
    if not endpoint:
        continue
    parsed = urlsplit(endpoint)
    assert parsed.scheme == 'https' and parsed.hostname and not parsed.username and not parsed.password
    assert not parsed.query and not parsed.fragment
    authority = parsed.netloc
    if not getattr(settings, prefix + '_force_path_style', True):
        bucket = getattr(settings, prefix + '_bucket')
        assert bucket
        authority = bucket + '.' + authority
    viewer_origins.add('https://' + authority)
config['ETL_VIEWER_FRAME_ORIGINS'] = ' '.join(sorted(viewer_origins))
config.update({
    'APP_PROJECT': 'etl-app', 'BACKEND_IMAGE': raw.get('BACKEND_IMAGE', 'etl-portable-backend:local'),
    'FRONTEND_IMAGE': raw.get('FRONTEND_IMAGE', 'etl-portable-frontend:local'),
    'DATABASE_MODE': 'neon-native-mongo' if native_mongo else 'neon-atlas',
    'ETL_DATA_NETWORK': 'etl-master-only_default', 'WEB_PORT': '8080',
    # Legacy Compose interpolations are replaced by compose.env-secrets.yml.
    'DOCKER_ENV_SECRETS': 'true',
    'ENV_SECRETS_DIR': env_secrets_dir.as_posix(),
    'CONNECTIONS_FILE': 'env-managed',
    'SOURCE_DATABASE_CA_FILE': ca.as_posix(),
    'MODEL_DIR': host(raw['INDEX_EMBEDDING_MODEL_PATH']).as_posix(),
    'MODEL_MANIFEST_FILE': (host(raw['INDEX_EMBEDDING_MODEL_PATH']) / 'etl-model-manifest.json').as_posix(),
    'SOURCE_LOCAL_DIR': source_dir.as_posix(),
    'WHISPER_MODEL_DIR': host(raw['WHISPER_MODEL_PATH']).as_posix(),
    'DRIVE_CREDENTIALS_FILE': 'env-managed',
    'ADMIN_PASSWORD_HASH_SECRET_FILE': 'env-managed',
    'SESSION_SIGNING_KEY_SECRET_FILE': 'env-managed',
    'AUTH_PROXY_KEY_SECRET_FILE': 'env-managed',
    'SOURCE_WRITE_COMPOSE_OVERRIDE_FILE': (private / 'runtime.override.yml').as_posix(),
    'MEDIA_ENABLED': 'true', 'INSTALL_MEDIA': 'true', 'INSTALL_SEMANTIC': 'false',
    'API_MEMORY_LIMIT': '6g', 'OCR_MEMORY_LIMIT': '6g',
    'API_CPUS': raw.get('API_CPUS', '4'),
})
# Optional graph profile uses a separate secret/network. Never add it to the
# four-key database connection file or inject it into OCR worker transactions.
for key, default in {
    'NEO4J_ENABLED': 'false', 'NEO4J_SEARCH_ENABLED': 'false',
    'NEO4J_SEARCH_MODE': 'shadow', 'NEO4J_DATABASE': 'neo4j', 'NEO4J_USER': 'neo4j',
    'NEO4J_URI_DOCKER': 'bolt://neo4j:7687',
    'NEO4J_QUERY_TIMEOUT_SECONDS': '3', 'NEO4J_CONNECT_TIMEOUT_SECONDS': '5',
    'NEO4J_SYNC_INTERVAL_SECONDS': '300',
    'NEO4J_DOCKER_IMAGE': 'neo4j:5.26.18-community',
    'NEO4J_BIND_ADDRESS': '127.0.0.1', 'NEO4J_HTTP_PORT': '7474', 'NEO4J_BOLT_PORT': '7687',
    'USERS_REFRESH_SOURCE_ID': 'postgresql',
    'USERS_REFRESH_HEALTH_MAX_AGE_SECONDS': '80',
    'GRAPH_WORKER_HEALTH_MAX_AGE_SECONDS': '420',
    'GRAPH_SERVICES_IMAGE': config['BACKEND_IMAGE'],
}.items():
    config[key] = raw.get(key, default)
config['NEO4J_PASSWORD_SECRET_FILE'] = 'env-managed'
config['NEO4J_AUTH_FILE'] = 'env-managed'
config['NEO4J_STATE_HOST_DIR'] = host(raw.get('NEO4J_STATE_HOST_DIR', '.local/neo4j')).as_posix()
for key, file_key in {
    'ADMIN_PASSWORD_HASH': 'ADMIN_PASSWORD_HASH_FILE',
    'SESSION_SIGNING_KEY': 'SESSION_SIGNING_KEY_FILE',
    'AUTH_PROXY_KEY': 'AUTH_PROXY_KEY_FILE',
    'NEO4J_PASSWORD': 'NEO4J_PASSWORD_SECRET_FILE',
}.items():
    value = config_secret(key, file_key)
    if value:
        private_values[key] = value
if private_values.get('NEO4J_PASSWORD'):
    private_values['NEO4J_AUTH'] = raw.get('NEO4J_AUTH') or (
        config['NEO4J_USER'] + '/' + private_values['NEO4J_PASSWORD'])
config.update(private_values)
# MASTER-only Compose can also be operated with docker.env without adding a
# second hand-maintained database password. These are not passed to workers.
for key in ('POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD',
            'MASTER_ONLY_PROJECT', 'MASTER_ONLY_PORT'):
    if key in raw:
        config[key] = raw[key]
def service_environment(name, env):
    """Keep graph access in API/dedicated graph services, never OCR/sync jobs."""
    per_env = {key: '${' + key + '}' for key in env if not key.startswith('AUTH_') and key != 'ADMIN_ENABLED'}
    per_env['INDEX_EMBEDDING_MODEL_PATH'] = '/opt/models/minilm' if name in ['api','ocr-worker'] else ''
    if name != 'api':
        per_env.update(NEO4J_ENABLED='false', NEO4J_SYNC_ENABLED='false',
                       NEO4J_SEARCH_ENABLED='false', NEO4J_RELATED_MODE='disabled')
    if name not in ['api','reconciliation-worker']:
        per_env.update(SOURCE_METADATA_WRITEBACK_ENABLED='false', SOURCE_METADATA_WRITER_MODE='disabled',
                       SOURCE_VERSION_WRITEBACK_ENABLED='false', SOURCE_STORAGE_WRITE_ENABLED='false')
    return per_env


services = {}
for name in ['api', 'master-sync-worker', 'source-file-worker', 'ocr-worker', 'reconciliation-worker', 'preflight']:
    per_env = service_environment(name, env)
    volumes = []
    if name in ['api','ocr-worker']:
        volumes.append({'type':'bind','source':host(raw['TRANSLATION_MODEL_ROOT']).as_posix(),
                        'target':'/opt/models/argos','read_only':True,'bind':{'create_host_path':False}})
    services[name] = {'environment':per_env, 'secrets':mounts, 'volumes':volumes}
services['api']['volumes'].extend([
    {'type':'bind','source':(root/'storage').as_posix(),'target':'/var/lib/etl/local','bind':{'create_host_path':False}},
    {'type':'bind','source':host(raw['AUTH_STATE_PATH']).parent.as_posix(),'target':'/var/lib/etl/auth','bind':{'create_host_path':False}},
])
services['ocr-worker']['volumes'].append({'type':'bind','source':(root/'storage').as_posix(),
    'target':'/var/lib/etl/local','bind':{'create_host_path':False}})
services['reconciliation-worker']['volumes'].append({'type':'bind','source':(root/'storage').as_posix(),
    'target':'/var/lib/etl/local','bind':{'create_host_path':False}})
(private/'runtime.override.yml').write_text(yaml.safe_dump({'services':services,'secrets':secrets},sort_keys=False),encoding='utf-8')
# Docker Compose single quotes prevent expansion of embedded JSON/$ values.
def quoted(value):
    value = str(value)
    assert '\n' not in value and '\r' not in value
    return "'" + value.replace('\\', '\\\\').replace("'", "\\'") + "'"


def configuration_section(key):
    if key in {'BACKEND_IMAGE', 'FRONTEND_IMAGE', 'GRAPH_SERVICES_IMAGE', 'APP_PROJECT',
               'DATABASE_MODE', 'ETL_DATA_NETWORK', 'WEB_PORT', 'SOURCE_WRITE_COMPOSE_OVERRIDE_FILE',
               'DOCKER_ENV_SECRETS', 'ENV_SECRETS_DIR', 'MEDIA_ENABLED', 'INSTALL_MEDIA',
               'INSTALL_SEMANTIC', 'API_MEMORY_LIMIT', 'OCR_MEMORY_LIMIT', 'API_CPUS',
               'MASTER_ONLY_PROJECT', 'MASTER_ONLY_PORT'}:
        return 'Deployment / images / runtime artifacts'
    if key.startswith(('AUTH_', 'ADMIN_', 'SESSION_SIGNING_')):
        return 'Authentication - existing identities and keys'
    if key.startswith(('NEO4J_', 'GRAPH_WORKER_')):
        return 'Neo4j - independent graph service'
    if key.startswith(('POSTGRES_', 'DATABASE_', 'MONGO_', 'MASTER_MONGO_', 'SOURCE_MONGO_')) or key in {'CONNECTIONS_FILE', 'ETL_CONNECTIONS_JSON', 'SOURCE_DATABASE_URL'}:
        return 'Database connections - private credentials'
    if key.startswith(('USERS_REFRESH_', 'MASTER_SYNC_', 'SYNC_', 'RECONCILIATION_')):
        return 'Workers / schedules / concurrency'
    if key.startswith('SEARCH_'):
        return 'Search - preserve ranking policy'
    if key.startswith(('INDEX_', 'VECTOR_', 'CHUNK_')) or key in {'MODEL_DIR', 'MODEL_MANIFEST_FILE'}:
        return 'BGE-M3 1024D / chunks / indexing'
    if key.startswith(('OCR_', 'TESSERACT_', 'PROCESSING_', 'OFFICE_', 'FFMPEG_', 'FFPROBE_', 'WHISPER_', 'TRANSLATION_')):
        return 'OCR / media / processing tools'
    if key.startswith('SOURCE_DRIVE_') or key == 'DRIVE_CREDENTIALS_FILE':
        return 'Google Drive - only used when enabled'
    if key.startswith(('SOURCE_POSTGRES_', 'AWS_')):
        return 'Legacy compatibility - not active SOURCE Docker'
    if key.startswith(('SOURCE_OCR_', 'SOURCE_WRITE_', 'SOURCE_VERSION_', 'SOURCE_METADATA_')):
        return 'SOURCE approved processing and write authority'
    if key.startswith(('SOURCE_STORAGE_', 'SOURCE_FILE_', 'ETL_FILE_', 'ETL_VIEWER_')):
        return 'Storage capabilities / credentials'
    return 'Application / SOURCE registry / paths'
# Launcher profile parsers accept quotes, while their override-path parser is
# intentionally simple: write this path unquoted (it contains no spaces here).
lines = ['# PRIVATE: generated from .env; never print or commit this file.',
         '# Launcher materializes scoped runtime secrets automatically from this env.',
         '# Contracts, CA, models and persistent state remain file data.']
sections = {}
for key, value in config.items():
    sections.setdefault(configuration_section(key), []).append(
        key+'='+(str(value) if key=='SOURCE_WRITE_COMPOSE_OVERRIDE_FILE' else quoted(value)))
for title, entries in sections.items():
    lines.extend(['', '# --- ' + title + ' ---', *entries])
(root/'docker.env').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Configured docker.env; project-private runtime mounts; Neon + Docker MASTER + '
      + ('Windows native MongoDB.' if native_mongo else 'Atlas.'))
print('Approved contracts copied by reference; runtime secrets generated from env; values withheld.')
