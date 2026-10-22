"""Offline management CLI: reads passwords from TTY/stdin, never argv or logs."""

import argparse
import getpass
import os
import secrets
import sys
from pathlib import Path

from argon2 import PasswordHasher


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init', 'hash'])
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--password-stdin', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    try:
        if args.action == 'init':
            if not args.directory:
                raise ValueError('AUTH_OUTPUT_DIRECTORY_REQUIRED')
            files = {name: args.directory / name for name in ('admin_password_hash', 'session_signing_key', 'auth_proxy_key')}
            args.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        else:
            if not args.output:
                raise ValueError('AUTH_OUTPUT_FILE_REQUIRED')
            files = {'admin_password_hash': args.output}
        if any(path.exists() for path in files.values()):
            raise ValueError('AUTH_SECRET_OUTPUT_EXISTS')
        password = sys.stdin.readline().rstrip('\r\n') if args.password_stdin else getpass.getpass('Admin password: ')
        if not args.password_stdin and password != getpass.getpass('Confirm password: '):
            raise ValueError('AUTH_PASSWORD_CONFIRMATION_FAILED')
        if not 15 <= len(password) <= 1024:
            raise ValueError('AUTH_PASSWORD_LENGTH_INVALID')
        values = {'admin_password_hash': PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2).hash(password)}
        if args.action == 'init':
            values.update(session_signing_key=secrets.token_hex(32), auth_proxy_key=secrets.token_hex(32))
        for name, path in files.items():
            with path.open('x', encoding='ascii') as stream:
                stream.write(values[name] + '\n')
            path.chmod(0o640 if name == 'auth_proxy_key' else 0o600)
        print('PASS: auth secret files created; password not displayed.')
        return 0
    except (OSError, ValueError) as error:
        code = str(error) if isinstance(error, ValueError) else 'AUTH_SECRET_WRITE_FAILED'
        print(code, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
