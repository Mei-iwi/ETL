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

connections = {
    'DATABASE_URL': str(settings.database_url).replace('@127.0.0.1:5434/', '@master-postgres:5432/'),
    'SOURCE_DATABASE_URL': linux_url(str(settings.source_database_url)),
    'MONGO_URI': settings.mongo_uri,
    'MONGO_DATABASE': settings.mongo_database,
}
assert '@master-postgres:5432/' in connections['DATABASE_URL']
assert connections['MONGO_DATABASE'] == 'etl_content'
(private / 'connections.json').write_text(json.dumps(connections), encoding='utf-8')
source_query = dict(parse_qsl(urlsplit(str(settings.source_database_url)).query))
ca = host(source_query['sslrootcert'])
assert ca.is_file()

# Emit settings only; credential values go to mounted secret files, never YAML.
env = {}
for key, value in raw.items():
    if key.lower() not in Settings.model_fields:
        continue
    if any(word in key for word in ('PASSWORD', 'SIGNING_KEY', 'PROXY_KEY', 'ACCESS_KEY', 'SECRET_KEY')):
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
    value = raw.get(key)
    if not value:
        existing = raw.get(target) or raw.get(key + '_FILE')
        if existing:
            env[target] = mount_secret('project_' + key.lower(), existing)
        continue
    if key == 'SOURCE_WRITE_DATABASE_URL':
        value = linux_url(value)
    file = private / (key.lower() + '.secret')
    file.write_text(value, encoding='utf-8')
    env[target] = mount_secret('project_' + key.lower(), file)
env['SOURCE_WRITE_DATABASE_URL_FILE'] = env['SOURCE_WRITE_DATABASE_URL_SECRET_FILE']
env['SOURCE_FILE_STORAGE_ACCESS_KEY_ID_FILE'] = env['ETL_FILE_STORAGE_ACCESS_KEY_ID_FILE']
env['SOURCE_FILE_STORAGE_SECRET_ACCESS_KEY_FILE'] = env['ETL_FILE_STORAGE_SECRET_ACCESS_KEY_FILE']
env['SOURCE_DRIVE_CREDENTIALS_FILE'] = '/run/secrets/drive.json'
if raw.get('SOURCE_DRIVE_ENABLED') == 'true':
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
    'APP_PROJECT': 'etl-app', 'BACKEND_IMAGE': 'etl-portable-backend:local',
    'FRONTEND_IMAGE': 'etl-portable-frontend:local', 'DATABASE_MODE': 'neon-atlas',
    'ETL_DATA_NETWORK': 'etl-master-only_default', 'WEB_PORT': '8080',
    'CONNECTIONS_FILE': (private / 'connections.json').as_posix(),
    'SOURCE_DATABASE_CA_FILE': ca.as_posix(),
    'MODEL_DIR': host(raw['INDEX_EMBEDDING_MODEL_PATH']).as_posix(),
    'MODEL_MANIFEST_FILE': (host(raw['INDEX_EMBEDDING_MODEL_PATH']) / 'etl-model-manifest.json').as_posix(),
    'SOURCE_LOCAL_DIR': source_dir.as_posix(),
    'WHISPER_MODEL_DIR': host(raw['WHISPER_MODEL_PATH']).as_posix(),
    'DRIVE_CREDENTIALS_FILE': host(raw['SOURCE_DRIVE_CREDENTIALS_FILE']).as_posix(),
    'ADMIN_PASSWORD_HASH_SECRET_FILE': host(raw['ADMIN_PASSWORD_HASH_FILE']).as_posix(),
    'SESSION_SIGNING_KEY_SECRET_FILE': host(raw['SESSION_SIGNING_KEY_FILE']).as_posix(),
    'AUTH_PROXY_KEY_SECRET_FILE': host(raw['AUTH_PROXY_KEY_FILE']).as_posix(),
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
    'USERS_REFRESH_SOURCE_ID': 'postgresql',
    'USERS_REFRESH_HEALTH_MAX_AGE_SECONDS': '80',
    'GRAPH_WORKER_HEALTH_MAX_AGE_SECONDS': '420',
    'GRAPH_SERVICES_IMAGE': 'etl-portable-backend:local',
}.items():
    config[key] = raw.get(key, default)
graph_auth = Path(raw.get('NEO4J_AUTH_FILE', '../ETL_Secrets/neo4j/auth.secret'))
if not graph_auth.is_absolute():
    graph_auth = root / 'deploy' / graph_auth
config['NEO4J_PASSWORD_SECRET_FILE'] = graph_auth.resolve().with_name('password.secret').as_posix()
config['NEO4J_STATE_HOST_DIR'] = host(raw.get('NEO4J_STATE_HOST_DIR', '.local/neo4j')).as_posix()
services = {}
for name in ['api', 'master-sync-worker', 'source-file-worker', 'ocr-worker', 'reconciliation-worker', 'preflight']:
    per_env = {key: '${' + key + '}' for key in env if not key.startswith('AUTH_') and key != 'ADMIN_ENABLED'}
    per_env['INDEX_EMBEDDING_MODEL_PATH'] = '/opt/models/minilm' if name in ['api','ocr-worker'] else ''
    if name not in ['api','reconciliation-worker']:
        per_env.update(SOURCE_METADATA_WRITEBACK_ENABLED='false', SOURCE_METADATA_WRITER_MODE='disabled',
                       SOURCE_VERSION_WRITEBACK_ENABLED='false', SOURCE_STORAGE_WRITE_ENABLED='false')
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
    assert '\n' not in value and '\r' not in value and "'" not in value
    return "'" + value + "'"
# Launcher profile parsers accept quotes, while their override-path parser is
# intentionally simple: write this path unquoted (it contains no spaces here).
lines = ['# Private Docker configuration generated from the current authorized .env.',
         '# Credentials remain in ETL_Secrets/docker and are mounted at runtime.']
for key,value in config.items():
    lines.append(key+'='+(str(value) if key=='SOURCE_WRITE_COMPOSE_OVERRIDE_FILE' else quoted(value)))
(root/'docker.env').write_text('\n'.join(lines)+'\n',encoding='utf-8')
print('Configured docker.env; project-private runtime mounts; Neon + Docker MASTER + Atlas.')
print('Approved contracts copied by reference; credential values withheld.')
