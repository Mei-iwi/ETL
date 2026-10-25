"""Preview/rebuild selected ContentBags through normal version locks and index jobs.

Default is read-only. Apply requires a new backup path for prior derived bags.
Source content is never rewritten and unselected queued jobs are never consumed.
"""

import argparse
import json
import os
from pathlib import Path
from time import sleep

from bson import json_util

from data_sync_etl.composition import Container
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.meaning_bags import sources


def run(container, version_ids, *, apply=False, backup=None):
    if not version_ids or len(version_ids) > 100 or len(set(version_ids)) != len(version_ids):
        raise ValueError('Select 1..100 distinct resource version IDs')
    index = container.indexing
    if not index.settings.index_meaning_bags_enabled:
        raise ValueError('INDEX_MEANING_BAGS_ENABLED must be true for this operation')
    planned = []
    for version_id in version_ids:
        with index.uow() as repo:
            snapshot = index._snapshot(repo, version_id)
            runs = index._runs(repo, version_id)
        if any(row['status'] in ('PENDING', 'RUNNING') for row in runs):
            raise ValueError('A selected version already has pending/running indexing')
        roots = index._roots(snapshot)
        nodes = {unit['id']: unit for unit in snapshot[2]}
        counts = [len(sources([(leaf, snapshot[4][leaf].text) for leaf in leaves], root, nodes[root]['title']))
                  for root, leaves in roots.items()]
        planned.append({'version_id': version_id, 'source_fingerprint': snapshot[3],
                        'bags': len(roots), 'meaning_members': sum(counts)})
    if not apply:
        return {'mode': 'dry-run', 'versions': planned, 'model_calls': 0}
    if backup is None or backup.exists():
        raise ValueError('Apply requires a new --backup path; existing backups are never overwritten')
    index._runtime()
    index.embedding.prepare()
    old_bags = list(index.bags.database.content_bags.find({'resource_version_id': {'$in': version_ids}}).limit(10001))
    if len(old_bags) > 10000:
        raise ValueError('Backup exceeds 10000 bags; select fewer versions')
    backup.parent.mkdir(parents=True, exist_ok=True)
    payload = {'versions': planned, 'bags': old_bags, 'model': {
        'id': index.embedding.model_id, 'revision': index.embedding.version, 'dimension': index.embedding.dimension},
        'bag_collection_options': next(index.bags.database.list_collections(filter={'name': 'content_bags'}))['options']}
    with backup.open('x', encoding='utf-8') as output:
        output.write(json_util.dumps(payload))
        output.flush()
        os.fsync(output.fileno())
    results = []
    for version_id in version_ids:
        print(json.dumps({'event': 'reindex-start', 'version_id': version_id}), flush=True)
        for attempt in range(10):
            try:
                queued = index.enqueue(version_id, force=True)
                break
            except IndexingError as exc:
                if exc.code != 'INDEX_ALREADY_RUNNING' or attempt == 9:
                    raise
                sleep(1)
        if not index.once(version_id):
            raise ValueError('Selected version is busy; retry through normal indexing')
        status = index.status(version_id)
        results.append({'version_id': version_id, 'run_id': queued['index_run_id'], 'status': status['index_status']})
        if status['index_status'] != 'READY':
            raise ValueError('Meaning build failed; inspect index status and keep the backup')
        print(json.dumps({'event': 'reindex-ready', **results[-1]}), flush=True)
    return {'mode': 'apply', 'versions': results, 'backup': str(backup)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selected = parser.add_mutually_exclusive_group(required=True)
    selected.add_argument('--version-id', action='append')
    selected.add_argument('--all-current', action='store_true')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error('--threads must be positive')
    if args.apply:
        import torch

        torch.set_num_threads(args.threads)
    container = Container()
    try:
        version_ids = args.version_id
        if args.all_current:
            rows = list(container.indexing.bags.database.content_bags.find(
                {'is_current': True}, {'resource_version_id': 1}).limit(10001))
            if len(rows) > 10000:
                raise ValueError('Too many bags for one deployment; select versions explicitly')
            version_ids = sorted({row['resource_version_id'] for row in rows})
        result = run(container, version_ids, apply=args.apply, backup=args.backup)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        container.close()


if __name__ == '__main__':
    main()
