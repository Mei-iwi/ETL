"""Render private env credentials into read-only Compose secret mounts.

These files are runtime artifacts, never an independent configuration source.
No account/password rotation or database access is performed here.
"""
import argparse
import json
import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values


SECRET_NAMES = {
    'ETL_CONNECTIONS_JSON': 'connections.json',
    'ADMIN_PASSWORD_HASH': 'admin_password_hash.secret',
    'SESSION_SIGNING_KEY': 'session_signing_key.secret',
    'AUTH_PROXY_KEY': 'auth_proxy_key.secret',
    'NEO4J_PASSWORD': 'neo4j_password.secret',
    'NEO4J_AUTH': 'neo4j_auth.secret',
    'SOURCE_DRIVE_CREDENTIALS_JSON': 'drive.json',
    'SOURCE_WRITE_DATABASE_URL': 'source_write_database_url.secret',
    'ETL_FILE_STORAGE_ACCESS_KEY_ID': 'etl_file_storage_access_key_id.secret',
    'ETL_FILE_STORAGE_SECRET_ACCESS_KEY': 'etl_file_storage_secret_access_key.secret',
    'SOURCE_STORAGE_WRITE_ACCESS_KEY_ID': 'source_storage_write_access_key_id.secret',
    'SOURCE_STORAGE_WRITE_SECRET_ACCESS_KEY': 'source_storage_write_secret_access_key.secret',
}


def materialize(values, root):
    values = dict(values)
    connection_keys = {'DATABASE_URL', 'SOURCE_DATABASE_URL', 'MONGO_URI', 'MONGO_DATABASE'}
    # Direct env variables are the editable source of truth. Retain the old
    # JSON input for existing deployments, but reject conflicting copies.
    if any(values.get(key) for key in connection_keys - {'MONGO_DATABASE'}):
        if not all(values.get(key) for key in connection_keys):
            raise ValueError('PRIVATE_ENV_CONNECTIONS_INCOMPLETE')
        connections = {key: values[key] for key in sorted(connection_keys)}
        if values.get('ETL_CONNECTIONS_JSON') and json.loads(values['ETL_CONNECTIONS_JSON']) != connections:
            raise ValueError('PRIVATE_ENV_CONNECTIONS_CONFLICT')
        values['ETL_CONNECTIONS_JSON'] = json.dumps(connections)
    root = Path(root).resolve()
    target = Path(values.get('ENV_SECRETS_DIR') or root / 'ETL_Secrets/docker/runtime-env-secrets')
    target = target if target.is_absolute() else root / target
    # Never let a configuration typo redirect credential writes outside this
    # deployment, or replace a credential through a pre-existing symlink.
    target.resolve().relative_to(root)
    target.mkdir(parents=True, exist_ok=True)
    prepared = []
    for key, name in SECRET_NAMES.items():
        value = values.get(key)
        if not value:
            continue
        if len(value.encode('utf-8')) > (65536 if key in {'ETL_CONNECTIONS_JSON', 'SOURCE_DRIVE_CREDENTIALS_JSON'} else 4096):
            raise ValueError('PRIVATE_ENV_SECRET_INVALID')
        if key == 'ETL_CONNECTIONS_JSON':
            data = json.loads(value)
            if (not isinstance(data, dict)
                    or set(data) != {'DATABASE_URL', 'SOURCE_DATABASE_URL', 'MONGO_URI', 'MONGO_DATABASE'}
                    or not all(isinstance(v, str) and v.strip() for v in data.values())):
                raise ValueError('PRIVATE_ENV_CONNECTIONS_INVALID')
        path = target / name
        if path.is_symlink():
            raise ValueError('PRIVATE_ENV_SECRET_SYMLINK_FORBIDDEN')
        prepared.append((path, value.encode('utf-8')))
    for path, value in prepared:
        if path.exists() and path.read_bytes() == value:
            continue
        # Atomic replacement avoids truncated credentials if preparation is
        # interrupted. Launchers recreate containers to pick up new mounts.
        descriptor, temporary = tempfile.mkstemp(prefix='.env-secret-', dir=target)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(value)
            os.replace(temporary, path)
            path.chmod(0o600)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return len(prepared)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', required=True)
    parser.add_argument('--graph-only', action='store_true')
    args = parser.parse_args()
    try:
        values = dotenv_values(args.env_file)
        if args.graph_only:
            values = {key: value for key, value in values.items()
                      if key in {'ENV_SECRETS_DIR', 'NEO4J_PASSWORD', 'NEO4J_AUTH'}}
        materialize(values, Path(__file__).resolve().parents[2])
        print('PRIVATE_ENV_RUNTIME_SECRETS_READY')
        return 0
    except (OSError, ValueError, TypeError):
        # Never print parsing/IO exceptions, source values, or connection URLs.
        print('PRIVATE_ENV_RUNTIME_SECRETS_INVALID')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
