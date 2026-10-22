"""Explicit local Auth DB maintenance. Never called by application startup."""

import argparse
import getpass
import json
import sys
from pathlib import Path

from argon2 import PasswordHasher, Type, extract_parameters

from data_sync_etl.config import Settings
from data_sync_etl.security.auth import AuthError, read_secret
from data_sync_etl.security.auth_database import AuthUserRepository, database_engine, email_key


def password(args):
    if args.hash_file:
        value = read_secret(args.hash_file, 'AUTH_PASSWORD_HASH_REQUIRED').decode('ascii')
        params = extract_parameters(value)
        if (params.type != Type.ID or params.version != 19 or not 19456 <= params.memory_cost <= 262144
                or not 2 <= params.time_cost <= 10 or not 1 <= params.parallelism <= 8
                or params.hash_len < 16 or params.salt_len < 16):
            raise ValueError
        return value
    plain = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('New password: ')
    if not args.password_stdin and plain != getpass.getpass('Confirm password: '):
        raise AuthError('AUTH_PASSWORD_CONFIRMATION_FAILED', 422)
    if not 15 <= len(plain) <= 1024:
        raise AuthError('AUTH_PASSWORD_LENGTH_INVALID', 422)
    return PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(plain)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['migrate', 'check-schema', 'create-admin', 'reset-admin-password', 'disable-admin', 'list-admin-users'])
    parser.add_argument('--url-file', type=Path, required=True)
    parser.add_argument('--confirm-auth-maintenance', action='store_true')
    parser.add_argument('--email')
    parser.add_argument('--display-name', default='Administrator')
    parser.add_argument('--hash-file', type=Path)
    parser.add_argument('--password-stdin', action='store_true')
    parser.add_argument('--unlock', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.action not in ('check-schema', 'list-admin-users') and not args.confirm_auth_maintenance:
            raise AuthError('AUTH_MAINTENANCE_APPROVAL_REQUIRED', 403)
        settings = Settings(_env_file=None, auth_database_url_file=args.url_file)
        repo = AuthUserRepository(database_engine(settings))
        if args.action == 'migrate':
            changed = repo.migrate()
            print(json.dumps({'status': 'PASS', 'version': 1, 'changed': changed}))
            return 0
        repo.check()
        if args.action == 'create-admin':
            email = email_key(args.email or settings.admin_login_email)
            existing = repo.get(email=email)
            if existing and 'admin' in existing['roles']:
                print(json.dumps({'status': 'PASS', 'created': False, 'id': existing['id']}))
                return 0
            account, created = repo.create(email, args.display_name, password(args), ['admin'], bootstrap=True)
            print(json.dumps({'status': 'PASS', 'created': created, 'id': account['id']}))
        elif args.action in ('reset-admin-password', 'disable-admin'):
            account = repo.get(email=email_key(args.email or ''))
            if not account or 'admin' not in account['roles']:
                raise AuthError('AUTH_USER_NOT_FOUND', 404)
            values = {'is_active': False} if args.action == 'disable-admin' else {
                'password_hash': password(args), 'must_change_password': True}
            if args.unlock:
                if args.action != 'reset-admin-password':
                    raise ValueError
                values.update(is_active=True, is_locked=False)
            repo.change(account['id'], values)
            print(json.dumps({'status': 'PASS', 'operation': args.action}))
        elif args.action == 'list-admin-users':
            page = 1
            while True:
                items, total = repo.list(page, 100)
                for item in items:
                    if 'admin' in item['roles']:
                        print(json.dumps({key: item[key] for key in ('id', 'email', 'display_name', 'is_active', 'is_locked')}))
                if page * 100 >= total:
                    break
                page += 1
        else:
            print(json.dumps({'status': 'PASS', 'version': 1}))
        return 0
    except AuthError as exc:
        print(json.dumps({'status': 'BLOCKED', 'code': exc.code}), file=sys.stderr)
        return 1
    except (OSError, ValueError, UnicodeError):
        print('{"status":"BLOCKED","code":"AUTH_MAINTENANCE_INVALID"}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
