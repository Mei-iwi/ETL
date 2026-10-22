"""Explicit owner-operated maintenance. Never invoked by app/worker startup."""
import json
import os
import sys
from pathlib import Path


def main():
    actions = {
        'master-migrate': ['python', '-m', 'alembic', 'upgrade', 'head'],
        'source-migrate': ['python', '-m', 'alembic', '-c', 'alembic_source.ini', 'upgrade', 'head'],
        'mongo-bootstrap-empty': ['python', '-m', 'data_sync_etl.adapters.mongo.schema', '--bootstrap-empty'],
    }
    action = sys.argv[1] if len(sys.argv) == 2 else ''
    if action not in actions or os.environ.get('ETL_MAINTENANCE_APPROVAL') != 'APPROVED_DATABASE_MAINTENANCE':
        print('BLOCKED: database-owner maintenance approval required', file=sys.stderr)
        return 2
    try:
        data = json.loads(Path('/run/secrets/ops-connections.json').read_text(encoding='utf-8'))
        allowed = {'DATABASE_URL', 'SOURCE_MIGRATION_DATABASE_URL', 'MONGO_URI', 'MONGO_DATABASE'}
        if not isinstance(data, dict) or set(data) != allowed or any(not isinstance(value, str) or 'CHANGE_ME' in value for value in data.values()):
            raise ValueError
        os.environ.update(data)
        os.chdir('/app')
        os.execvp(actions[action][0], actions[action])
    except Exception:
        print('BLOCKED: invalid owner maintenance configuration', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
