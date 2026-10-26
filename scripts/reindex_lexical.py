"""Offline lexical backfill. Default: read-only dry-run; never changes .env/model.

Stop all writers before apply/rollback. A durable BSON backup is also the resume
checkpoint. Existing dense vectors are reused only when source/model match.
"""

import argparse
import json
from pathlib import Path

from bson import json_util
from ulid import ULID

from data_sync_etl.adapters.mongo.lexical_schema import (
    LEGACY_ANALYZER,
    check_bag_schema,
    check_lexical_schema,
    is_legacy_concept_contract,
    lexical_contract,
)
from data_sync_etl.adapters.mongo.meaning_schema import meaning_contract
from data_sync_etl.adapters.mongo.schema import contracts
from data_sync_etl.composition import Container
from data_sync_etl.domain.core import now
from data_sync_etl.domain.indexing import digest, validate_vector
from data_sync_etl.domain.lexical import lexical_document
from data_sync_etl.domain.mongo_documents import ContentBagDocument, normalize_keywords


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json_util.dumps(value), encoding='utf-8')
    temporary.replace(path)


def target_identity(container):
    settings = container.settings
    return digest([settings.database_url, container.content_bags.database.name,
                   settings.index_embedding_model_id, settings.index_embedding_model_version,
                   settings.index_embedding_dimension])


def compatible_validators(dimension):
    return (contracts(dimension)['content_bags'], lexical_contract(dimension),
            lexical_contract(dimension, analyzer=LEGACY_ANALYZER), meaning_contract(dimension),
            meaning_contract(dimension, analyzer=LEGACY_ANALYZER))


def prepare_version(index, version_id, documents, *, own_run_id=None):
    """Prove old dense artifacts still represent the unchanged keyword input."""
    with index.uow() as repo:
        snapshot = index._snapshot(repo, version_id)
        runs = index._runs(repo, version_id)
    if any(run['status'] in ('PENDING', 'RUNNING') and run['id'] != own_run_id for run in runs):
        raise ValueError('In-flight indexing; stop/finish workers before migration')
    roots = index._roots(snapshot)
    if set(roots) != {doc['_id'] for doc in documents}:
        raise ValueError('Bag membership changed; full reindex required')
    source_fp = index.reader.content_fingerprint(version_id, generation=snapshot[1])
    nodes = {unit['id']: unit for unit in snapshot[2]}
    updated = []
    for raw in documents:
        bag = ContentBagDocument.model_validate({key: value for key, value in raw.items() if key != 'concepts'})
        leaves = roots[bag.id]
        keywords = normalize_keywords([term for leaf in leaves for term in snapshot[5][leaf]])
        if (not bag.is_current or bag.resource_version_id != version_id
                or bag.projection_generation != snapshot[1] or bag.content_fingerprint != source_fp
                or bag.unit_type != nodes[bag.id]['unit_type'] or bag.title != nodes[bag.id]['title']
                or bag.keywords != keywords or bag.embedding_text != ' | '.join(keywords)
                or bag.embedding_model != f'{index.embedding.model_id}@{index.embedding.version}'
                or any(snapshot[4][leaf].keywords != snapshot[5][leaf] for leaf in leaves)):
            raise ValueError('Source/model/artifact mismatch; full reindex required')
        validate_vector(bag.embedding, index.embedding.dimension)
        lexical = lexical_document([snapshot[4][leaf].text for leaf in leaves], bag.title)
        updated.append({**{key: value for key, value in raw.items() if key != 'concepts'},
                        'lexical': lexical.model_dump()})
    return snapshot, updated


def run(container, args):
    index, store = container.indexing, container.content_bags
    if store is None:
        raise ValueError('MongoDB ContentBag store required')
    database, dimension = store.database, store.dimension
    catalog = next(database.list_collections(filter={'name': 'content_bags'}))
    options = catalog.get('options', {})
    allowed = compatible_validators(dimension)
    legacy = is_legacy_concept_contract(options.get('validator', {}), dimension)
    if options.get('validator') not in allowed and not legacy:
        raise ValueError('Unknown validator; no migration performed')
    if args.rollback:
        return rollback(container, args)
    if database.content_bags.count_documents({'is_current': {'$ne': True}, 'concepts': {'$exists': True}}):
        raise ValueError('Historical concept bags require a separately reviewed migration')
    resume = args.backup is not None and args.backup.exists()
    if resume:
        journal = json_util.loads(args.backup.read_text(encoding='utf-8'),
                                  json_options=json_util.JSONOptions(tz_aware=True))
        if journal['target'] != target_identity(container) or journal['status'] == 'ROLLED_BACK':
            raise ValueError('Backup target/status mismatch')
        previous_validator = journal['options']['validator']
        if (previous_validator not in allowed
                and not is_legacy_concept_contract(previous_validator, dimension)):
            raise ValueError('Unknown backup validator')
        original = journal['bags']
    else:
        original = list(database.content_bags.find({'is_current': True}).sort('_id', 1).limit(10001))
        if len(original) > 10000:
            raise ValueError('Backfill exceeds 10000 bags; select a separately reviewed target')
    report = check_bag_schema(database, dimension=dimension,
        bag_validator=options['validator'], audit_documents=True,
        previous_bag_validator=journal['options']['validator'] if resume else None)
    if not report['ready']:
        raise ValueError('Schema/documents not ready; inspect read-only schema report')
    versions = sorted({doc['resource_version_id'] for doc in original})
    prepared = {}
    for version_id in versions:
        snapshot, rows = prepare_version(index, version_id,
            [doc for doc in original if doc['resource_version_id'] == version_id],
            own_run_id=journal['versions'][version_id]['run_id'] if resume else None)
        prepared[version_id] = (snapshot, rows)
    summary = {'mode': 'apply' if args.apply else 'dry-run', 'versions': len(versions),
               'bags': len(original), 'dense_vectors_reused': True, 'embedding_calls': 0,
               'legacy_concept_bags': sum('concepts' in row for row in original)}
    if not args.apply:
        return summary
    if not args.writers_stopped or args.backup is None:
        raise ValueError('--apply requires --writers-stopped and --backup PATH')
    if not resume:
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        journal = {'target': target_identity(container), 'status': 'PREPARED',
                   'options': options, 'bags': original, 'versions': {
                       key: {'fingerprint': value[0][3], 'run_id': str(ULID()), 'done': False}
                       for key, value in prepared.items()}}
        save(args.backup, journal)  # durable BEFORE any database write
    for version_id, (snapshot, _) in prepared.items():
        if snapshot[3] != journal['versions'][version_id]['fingerprint']:
            raise ValueError('Source changed since checkpoint; refusing resume')
    target_validator = (meaning_contract(dimension) if 'meanings' in options['validator']['$jsonSchema']['properties']
                        else lexical_contract(dimension))
    database.command('collMod', 'content_bags', validator=target_validator,
                     validationLevel='strict', validationAction='error')
    for version_id in versions:
        entry = journal['versions'][version_id]
        with index.uow.processing_lock(version_id) as acquired:
            if not acquired:
                raise ValueError('Version locked; stop writers before resuming')
            snapshot, rows = prepared[version_id]
            with index.uow() as repo:
                existing = repo.get('etl_stage_runs', entry['run_id'])
                if existing is None:
                    repo.insert('etl_stage_runs', {'id': entry['run_id'],
                        'resource_version_id': version_id, 'stage': 'INDEX_BUILD',
                        'status': 'RUNNING', 'input_fingerprint': snapshot[3],
                        'started_at': now(), 'created_at': now()})
            for row in rows:
                current = database.content_bags.find_one({'_id': row['_id']})
                expected_old = next(doc for doc in original if doc['_id'] == row['_id'])
                if current not in (expected_old, row):
                    raise ValueError('Concurrent bag change; refusing overwrite')
                database.content_bags.replace_one({'_id': row['_id']}, row)
            with index.uow() as repo:
                fresh = index._snapshot(repo, version_id)
                index._verified(fresh, {'input_fingerprint': snapshot[3]})
                repo.update('etl_stage_runs', entry['run_id'],
                            {'status': 'COMPLETED', 'finished_at': now(), 'error_message': None})
            entry['done'] = True
            save(args.backup, journal)
    journal['status'] = 'COMPLETE'
    save(args.backup, journal)
    return {**summary, 'status': 'COMPLETE'}


def rollback(container, args):
    if not args.writers_stopped or args.backup is None:
        raise ValueError('--rollback requires --writers-stopped and --backup PATH')
    journal = json_util.loads(args.backup.read_text(encoding='utf-8'),
                                  json_options=json_util.JSONOptions(tz_aware=True))
    if journal['target'] != target_identity(container):
        raise ValueError('Backup target mismatch')
    index, database = container.indexing, container.content_bags.database
    original_validator = journal['options']['validator']
    dimension = container.content_bags.dimension
    if (original_validator not in compatible_validators(dimension)
            and not is_legacy_concept_contract(original_validator, dimension)):
        raise ValueError('Unknown backup validator; refusing rollback')
    for version_id, entry in journal['versions'].items():
        with index.uow() as repo:
            snapshot = index._snapshot(repo, version_id)
        if snapshot[3] != entry['fingerprint']:
            raise ValueError('Source changed; restore requires separate review')
    # Restore the exact original payload, including retired concept data.
    for original in journal['bags']:
        current = database.content_bags.find_one({'_id': original['_id']})
        if current is None or {k: v for k, v in current.items() if k not in ('lexical', 'concepts')} != {
                k: v for k, v in original.items() if k not in ('lexical', 'concepts')}:
            raise ValueError('Bag changed since migration; refusing rollback overwrite')
        if 'concepts' in current and current['concepts'] != original.get('concepts'):
            raise ValueError('Concept payload changed since backup; refusing rollback')
    options = journal['options']
    # The original concept payload is forbidden by the replacement validator.
    # Restore the original validator before documents; writers stay stopped.
    database.command('collMod', 'content_bags', validator=options['validator'],
                     validationLevel=options.get('validationLevel', 'strict'),
                     validationAction=options.get('validationAction', 'error'))
    for original in journal['bags']:
        database.content_bags.replace_one({'_id': original['_id']}, original)
    for version_id, entry in journal['versions'].items():
        with index.uow() as repo:
            repo.delete('etl_stage_runs', {'id': entry['run_id'], 'resource_version_id': version_id,
                                           'stage': 'INDEX_BUILD'})
    journal['status'] = 'ROLLED_BACK'
    save(args.backup, journal)
    return {'mode': 'rollback', 'status': 'ROLLED_BACK'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--rollback', action='store_true')
    mode.add_argument('--check', action='store_true')
    parser.add_argument('--backup', type=Path)
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    container = Container()
    try:
        if args.check:
            result = check_lexical_schema(container.content_bags.database,
                dimension=container.settings.index_embedding_dimension, audit_documents=True)
        else:
            result = run(container, args)
        print(json.dumps(result, ensure_ascii=True, default=str))
        return int(result.get('ready') is False)
    finally:
        container.close()


if __name__ == '__main__':
    raise SystemExit(main())
