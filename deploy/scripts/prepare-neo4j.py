"""Derive Docker auth file from private .env without printing its contents.

Does not generate/rotate database credentials or touch existing graph data.
"""
from pathlib import Path

from dotenv import dotenv_values


def main():
    values = dotenv_values('.env')
    password = values.get('NEO4J_PASSWORD', '')
    user = values.get('NEO4J_USER', 'neo4j')
    target = values.get('NEO4J_AUTH_FILE')
    if user != 'neo4j' or len(password) < 12 or not target or '\n' in password:
        raise SystemExit('GRAPH_PRIVATE_CONFIG_REQUIRED')
    path = Path(target)
    if not path.is_absolute():
        path = Path('deploy') / path
    if path.exists() and path.read_text(encoding='utf-8') != user + '/' + password:
        raise SystemExit('GRAPH_AUTH_FILE_DIFFERS_NO_AUTOMATIC_ROTATION')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(user + '/' + password, encoding='utf-8')
    password_path = path.with_name('password.secret')
    if password_path.exists() and password_path.read_text(encoding='utf-8') != password:
        raise SystemExit('GRAPH_PASSWORD_FILE_DIFFERS_NO_AUTOMATIC_ROTATION')
    password_path.write_text(password, encoding='utf-8')
    print('GRAPH_AUTH_FILE_READY')


if __name__ == '__main__':
    main()
