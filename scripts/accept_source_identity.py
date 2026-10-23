"""Explicitly accept a recreated SOURCE after operator review; default dry-run.

Only creation timestamps and ownership checksums change here. Normal sync still
enforces all its guards and later imports the authoritative SOURCE contents.
"""
import argparse
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url

from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork, utc
from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
from data_sync_etl.application.sync import STREAMS
from data_sync_etl.config import Settings
from data_sync_etl.db.models import MODELS
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.source_ownership import state_hash


def target_identity(settings):
    def identity(url):
        parsed = make_url(url)
        return [parsed.drivername, parsed.host, parsed.port, parsed.database, parsed.username]
    return digest([identity(settings.database_url), identity(settings.source_database_url)])


def prepare(source, repo, *, lock=False, max_records=1000):
    changes, blocked, summary, source_fingerprints = [], [], {}, {}
    for stream, table in STREAMS.items():
        records = []
        for batch in source.batches(stream, None, 100):
            records.extend(batch)
            if len(records) > max_records:
                raise ValueError('Source exceeds the reviewed record limit')
        source_fingerprints[stream] = digest([r.model_dump() for r in records])
        counts = {'source_records': len(records), 'new_records': 0, 'identity_changes': 0,
                  'credential_hash_changes': 0}
        for aggregate in records:
            values = aggregate.values
            row = repo.get(table, values['id'], lock=lock)
            if row is None:
                counts['new_records'] += 1
                continue
            reasons = []
            if row['source_origin'] != 'POSTGRESQL':
                reasons.append('BLOCKED_SOURCE_OWNERSHIP')
            if not row['source_state_hash'] or row['source_state_hash'] != state_hash(row):
                reasons.append('BLOCKED_LOCAL_SOURCE_CHANGE')
            if stream in ('users', 'resources') and values.get('mongo_id') not in (None, row.get('mongo_id')):
                reasons.append('BLOCKED_MONGO_ID_MAPPING')
            if reasons:
                blocked.append({'stream': stream, 'id': row['id'], 'reasons': reasons})
                continue
            if stream == 'users':
                # Private equality check only; no hash/credential values enter the plan.
                model = repo.session.get(MODELS[table], row['id'])
                counts['credential_hash_changes'] += model.hashed_password != values['hashed_password']
            field = 'grade_created_at' if stream == 'grades' else 'created_at'
            if utc(row[field]) == utc(values[field]):
                continue
            after = {**row, field: utc(values[field])}
            changes.append({'stream': stream, 'table': table, 'id': row['id'], 'field': field,
                'before': utc(row[field]), 'after': utc(values[field]),
                'before_state_hash': row['source_state_hash'], 'after_state_hash': state_hash(after)})
            counts['identity_changes'] += 1
        summary[stream] = counts
    payload = {'summary': summary, 'changes': changes, 'blocked': blocked,
               'source_fingerprints': source_fingerprints}
    return {**payload, 'fingerprint': digest(payload),
            'status': 'BLOCKED' if blocked else 'READY' if changes else 'NO_CHANGES'}


def preview(source, uow):
    with uow() as repo:
        if repo.session.get_bind().dialect.name == 'postgresql':
            repo.session.execute(text('SET TRANSACTION READ ONLY'))
        return prepare(source, repo)


def apply(source, uow, fingerprint, backup, target):
    if not fingerprint:
        raise ValueError('An exact dry-run fingerprint is required')
    with uow.sync_lock() as acquired:
        if not acquired:
            raise ValueError('A sync is running; no changes made')
        with uow() as repo:
            plan = prepare(source, repo, lock=True)
            if plan['status'] != 'READY' or plan['fingerprint'] != fingerprint:
                raise ValueError('Plan changed or blocked; run dry-run again')
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open('x', encoding='utf-8') as output:
                output.write(json.dumps({'target': target, 'plan': plan}, default=str, indent=2) + '\n')
                output.flush()
                import os
                os.fsync(output.fileno())
            for change in plan['changes']:
                repo.update(change['table'], change['id'], {
                    change['field']: change['after'], 'source_state_hash': change['after_state_hash']})
    return {'status': 'APPLIED', 'rows': len(plan['changes']), 'fingerprint': fingerprint,
            'backup': str(backup), 'normal_sync_still_required': True}


def rollback(uow, backup, target):
    journal = json.loads(backup.read_text(encoding='utf-8'))
    if journal['target'] != target:
        raise ValueError('Backup target mismatch')
    with uow.sync_lock() as acquired:
        if not acquired:
            raise ValueError('A sync is running; no changes made')
        with uow() as repo:
            for change in journal['plan']['changes']:
                row = repo.get(change['table'], change['id'], lock=True)
                if (row is None or row['source_origin'] != 'POSTGRESQL'
                        or state_hash(row) != change['after_state_hash']
                        or row['source_state_hash'] != change['after_state_hash']):
                    raise ValueError('MASTER changed after apply; use the database backup for reviewed recovery')
            for change in journal['plan']['changes']:
                repo.update(change['table'], change['id'], {
                    change['field']: datetime.fromisoformat(change['before']),
                    'source_state_hash': change['before_state_hash']})
    return {'status': 'ROLLED_BACK', 'rows': len(journal['plan']['changes'])}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    parser.add_argument('--fingerprint')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if (args.apply or args.rollback) and args.backup is None:
        parser.error('--apply/--rollback require --backup PATH')
    settings = Settings()
    source = PostgreSQLSourceAdapter(settings.source_database_url)
    engine = make_engine(settings.database_url)
    uow = SQLAlchemyUnitOfWork(sessions(engine))
    try:
        if args.apply:
            result = apply(source, uow, args.fingerprint, args.backup, target_identity(settings))
        elif args.rollback:
            result = rollback(uow, args.backup, target_identity(settings))
        else:
            result = preview(source, uow)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open('x', encoding='utf-8') as output:
                json.dump(result, output, default=str, indent=2)
        # Do not print source fingerprints or connection/credential values.
        print(json.dumps({k: v for k, v in result.items() if k not in ('changes', 'source_fingerprints')}, default=str))
    finally:
        source.close()
        engine.dispose()


if __name__ == '__main__':
    main()
