"""Official MASTER login and bounded Search canary; no database/user mutation.

Credentials are read only from an owner-provided private configuration/file.
Never print response bodies, passwords, tokens, cookies or connection secrets.
This client measures requests; it does not enable graph flags or certify the
complete ACL/UI regression matrix by itself.
"""

import argparse
import json
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class CanaryProfile(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    email: str = Field(pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$', max_length=254)
    password_file: Path
    base_url: str = 'http://127.0.0.1:8080'
    query: str = Field(default='dao dong', min_length=1, max_length=1000)
    methods: list[Literal['bow', 'vector', 'hybrid']] = Field(
        default_factory=lambda: ['bow', 'vector', 'hybrid'], min_length=1, max_length=3)
    expected_role: Literal['ADMIN', 'USER', 'VIEWER'] = 'ADMIN'
    timeout_seconds: int = Field(default=30, ge=1, le=30)

    @model_validator(mode='after')
    def safe_origin(self):
        try:
            url = urlsplit(self.base_url)
            port = url.port
        except ValueError:
            raise ValueError('CANARY_ORIGIN_INVALID') from None
        if (url.scheme not in {'http', 'https'} or url.hostname not in {'127.0.0.1', 'localhost', '::1'}
                or url.username or url.password or url.query or url.fragment
                or url.path not in {'', '/'} or (port is not None and not 1 <= port <= 65535)
                or len(set(self.methods)) != len(self.methods)):
            raise ValueError('CANARY_ORIGIN_INVALID')
        return self


def password_for(profile):
    path = profile.password_file
    if path.is_symlink() or not path.is_file() or not 1 <= path.stat().st_size <= 8192:
        return None
    value = path.read_text(encoding='utf-8').strip()
    return value if value and '\n' not in value and '\r' not in value else None


def measure(profile, *, transport=None):
    password = password_for(profile)
    if password is None:
        return {'status': 'AUTH_BLOCKED', 'reason': 'PRIVATE_PASSWORD_NOT_PROVIDED'}
    origin = profile.base_url.rstrip('/')
    with httpx.Client(base_url=origin, headers={'Origin': origin}, transport=transport,
                      timeout=profile.timeout_seconds, follow_redirects=False, trust_env=False) as client:
        config = client.get('/api/v1/auth/config')
        if config.status_code != 200 or config.json().get('mode') != 'master_users':
            return {'status': 'AUTH_BLOCKED', 'reason': 'MASTER_USERS_MODE_REQUIRED'}
        probe = client.post('/api/v1/search', json={'query': 'probe', 'method': 'bow'})
        if probe.status_code not in (401, 403):
            return {'status': 'FAILED', 'reason': 'UNAUTHENTICATED_SEARCH_NOT_DENIED'}
        csrf = client.get('/api/v1/auth/csrf')
        csrf.raise_for_status()
        client.headers['X-CSRF-Token'] = csrf.json()['csrf_token']
        login = client.post('/api/v1/auth/login', json={
            'email': profile.email, 'password': password, 'provider': 'master', 'remember_me': False})
        del password
        if login.status_code != 200:
            return {'status': 'AUTH_BLOCKED', 'reason': 'MASTER_LOGIN_REJECTED', 'http_status': login.status_code}
        try:
            client.headers['X-CSRF-Token'] = client.get('/api/v1/auth/csrf').json()['csrf_token']
            me = client.get('/api/v1/auth/me')
            me.raise_for_status()
            identity = me.json()
            if (identity.get('mode') != 'master_users' or identity.get('role') != profile.expected_role
                    or not str(identity.get('id', '')).startswith('master:')
                    or identity.get('provenance') != 'master-readonly'):
                return {'status': 'AUTH_BLOCKED', 'reason': 'MASTER_IDENTITY_OR_ROLE_MISMATCH'}
            client.headers['X-CSRF-Token'] = client.get('/api/v1/auth/csrf').json()['csrf_token']
            cases = []
            for method in profile.methods:
                started = perf_counter()
                response = client.post('/api/v1/search', json={
                    'query': profile.query, 'method': method, 'page': 1, 'size': 5})
                entry = {'method': method, 'http_status': response.status_code,
                         'seconds': round(perf_counter()-started, 3)}
                if response.status_code == 200:
                    payload = response.json()
                    entry['result_digest'] = sha256(json.dumps(payload, sort_keys=True,
                        ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
                    entry['total'] = payload.get('total')
                    if payload.get('items'):
                        item = payload['items'][0]
                        url = item.get('context_url', '')
                        if not url.startswith('/api/v1/chunks/') or not url.endswith('/context'):
                            return {'status': 'FAILED', 'reason': 'CONTEXT_LOCATOR_INVALID'}
                        context = client.get(url, params={'neighbors': 0})
                        entry['context_status'] = context.status_code
                        if (context.status_code != 200
                                or context.json().get('content_unit_id') != item['content_unit_id']):
                            return {'status': 'FAILED', 'reason': 'CONTEXT_IDENTITY_MISMATCH'}
                cases.append(entry)
            return {'status': 'AUTHENTICATED_CANARY_MEASURED' if all(
                x['http_status'] == 200 for x in cases) else 'FAILED', 'cases': cases,
                'unauthenticated_search_denied': True, 'auth_mode': 'master_users'}
        finally:
            if client.post('/api/v1/auth/logout').status_code != 204:
                raise ValueError('CANARY_SESSION_REVOCATION_FAILED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=Path('.local/neo4j/auth-canary.json'))
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    try:
        if args.config.is_symlink() or not args.config.is_file() or args.config.stat().st_size > 65536:
            raise ValueError()
        profile = CanaryProfile.model_validate_json(args.config.read_text(encoding='utf-8'))
        result = ({'status': 'CONFIG_VALIDATED_NO_NETWORK'} if args.validate_only
                  else measure(profile))
    except (OSError, ValueError, KeyError, httpx.HTTPError):
        result = {'status': 'AUTH_BLOCKED', 'reason': 'PRIVATE_CONFIG_OR_AUTH_CANARY_UNAVAILABLE'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'reason': result.get('reason')}))
    return 0 if result['status'] in {'CONFIG_VALIDATED_NO_NETWORK', 'AUTHENTICATED_CANARY_MEASURED'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
