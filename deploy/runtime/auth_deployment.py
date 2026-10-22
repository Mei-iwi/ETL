"""Validate the effective Compose document, without emitting configuration values."""
import json
import sys
from urllib.parse import urlsplit


def validate(document):
    services = document['services']
    api, frontend = services['api'], services['frontend']
    env, web = api['environment'], frontend['environment']
    mode = env.get('AUTH_MODE')
    if mode not in ('single_admin', 'local_demo', 'db_users', 'master_users'):
        raise ValueError('AUTH_MODE_REQUIRED')
    if api.get('ports') or api.get('network_mode') or frontend.get('network_mode'):
        raise ValueError('AUTH_API_MUST_BE_PRIVATE')
    if api.get('deploy', {}).get('replicas', 1) != 1:
        raise ValueError('AUTH_SINGLE_INSTANCE_REQUIRED')
    if env.get('AUTH_ORIGIN') != web.get('AUTH_ORIGIN') or mode != web.get('AUTH_MODE'):
        raise ValueError('AUTH_GATEWAY_MISMATCH')
    origin = urlsplit(env['AUTH_ORIGIN'])
    ports = frontend.get('ports', [])
    if not ports or any(int(p['published']) != (origin.port or (443 if origin.scheme == 'https' else 80)) for p in ports):
        raise ValueError('AUTH_ORIGIN_PORT_MISMATCH')
    if env.get('AUTH_DEPLOYMENT_PROFILE') == 'localhost':
        if origin.hostname not in ('127.0.0.1', 'localhost') or any(p.get('host_ip') != '127.0.0.1' for p in ports):
            raise ValueError('AUTH_LOOPBACK_BIND_REQUIRED')
        if env.get('AUTH_PUBLIC_ENABLED') != 'false' or web.get('AUTH_TLS') != 'false':
            raise ValueError('AUTH_LOCAL_PROFILE_INVALID')
    elif env.get('AUTH_DEPLOYMENT_PROFILE') == 'multi_machine':
        if mode not in ('single_admin', 'db_users', 'master_users') or origin.scheme != 'https' or env.get('AUTH_COOKIE_SECURE') != 'true' or web.get('AUTH_TLS') != 'true':
            raise ValueError('AUTH_TLS_REQUIRED')
    else:
        raise ValueError('AUTH_DEPLOYMENT_PROFILE_REQUIRED')
    bypass = env.get('AUTH_DEV_ADMIN_BYPASS') == 'true'
    if mode == 'local_demo':
        if not bypass or env.get('APP_ENVIRONMENT') != 'local' or env.get('AUTH_DEPLOYMENT_PROFILE') != 'localhost':
            raise ValueError('AUTH_BYPASS_ISOLATION_REQUIRED')
    elif bypass:
        raise ValueError('AUTH_BYPASS_FORBIDDEN')
    for name, service in services.items():
        granted = {s['source'] if isinstance(s, dict) else s for s in service.get('secrets', [])}
        if name != 'api' and granted & {'admin_password_hash', 'session_signing_key', 'auth_database_url'}:
            raise ValueError('AUTH_ACCOUNT_SECRET_SCOPE_INVALID')
        if name not in ('api', 'frontend') and 'auth_proxy_key' in granted:
            raise ValueError('AUTH_PROXY_SECRET_SCOPE_INVALID')
    if any(not service.get('read_only') for name, service in services.items() if name in ('api', 'frontend')):
        raise ValueError('AUTH_READ_ONLY_REQUIRED')
    if mode == 'db_users':
        granted = {item['source'] if isinstance(item, dict) else item for item in api.get('secrets', [])}
        if 'auth_database_url' not in granted or 'admin_password_hash' in granted:
            raise ValueError('AUTH_DATABASE_SECRET_SCOPE_INVALID')
        if env.get('AUTH_DATABASE_URL_FILE') != '/run/secrets/auth_database_url':
            raise ValueError('AUTH_DATABASE_SECRET_REQUIRED')


def main():
    try:
        validate(json.load(sys.stdin))
        print('{"status":"PASS","operation":"auth_deployment_boundary"}')
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, ValueError) and str(error).replace('_', '').isalnum() else 'AUTH_DEPLOYMENT_INVALID'
        print(json.dumps({'status': 'BLOCKED', 'code': code}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
