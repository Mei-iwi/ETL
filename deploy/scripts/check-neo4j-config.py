"""Validate standalone deployment without printing resolved Compose/secrets."""

import argparse
import re
from pathlib import Path

from dotenv import dotenv_values


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--env-file', default='.env')
    args = parser.parse_args()
    values = dotenv_values(args.env_file)
    if values.get('NEO4J_BIND_ADDRESS', '127.0.0.1') not in {'127.0.0.1', '::1'}:
        raise SystemExit('GRAPH_LOOPBACK_BIND_REQUIRED')
    image = values.get('NEO4J_DOCKER_IMAGE', 'neo4j:5.26.18-community')
    if not re.fullmatch(r'neo4j:(?:\d+\.){2}\d+-community|neo4j@sha256:[0-9a-f]{64}', image):
        raise SystemExit('GRAPH_PINNED_COMMUNITY_IMAGE_REQUIRED')
    try:
        ports = [int(values.get(key, default)) for key, default in
                 [('NEO4J_BOLT_PORT', '7687'), ('NEO4J_HTTP_PORT', '7474')]]
        if any(not 1024 <= p <= 65535 for p in ports) or len(set(ports)) != 2:
            raise ValueError()
        auth = values.get('NEO4J_AUTH')
        if not auth:
            secret = Path(values.get('NEO4J_AUTH_FILE', ''))
            if not secret.is_absolute():
                secret = Path('deploy') / secret
            if secret.is_symlink() or not secret.is_file() or secret.stat().st_size > 4096:
                raise ValueError()
            auth = secret.read_text(encoding='utf-8')
        user, password = auth.split('/', 1)
        if user != 'neo4j' or len(password) < 12 or '\n' in password:
            raise ValueError()
        if values.get('NEO4J_PASSWORD') and password != values['NEO4J_PASSWORD']:
            raise ValueError()
    except (OSError, ValueError, UnicodeError):
        raise SystemExit('GRAPH_PRIVATE_RUNTIME_CONFIG_INVALID') from None
    print('GRAPH_RUNTIME_CONFIG_VALID')


if __name__ == '__main__':
    main()
