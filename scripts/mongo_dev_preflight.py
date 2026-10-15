"""Read-only Dev plan and opt-in backup/restore rehearsal. NEVER writes Dev schema.

Uses the existing contracts/checker/installer. No HTTP/startup/worker integration.
"""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from bson import BSON, json_util
from bson.codec_options import CodecOptions
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri

from data_sync_etl.adapters.mongo.schema import INDEXES, check_schema, contracts, initialize
from data_sync_etl.config import Settings
from data_sync_etl.domain.mongo_documents import (
    ContentBagDocument,
    ContentUnitDocument,
    OCRPageDocument,
    ResourceDescriptionDocument,
    UserProfileDocument,
)


def fingerprint(value):
    # Do not sort nested keys: compound-index key order is semantically significant.
    return hashlib.sha256(json_util.dumps(value).encode('utf-8')).hexdigest()


def command(*args):
    return subprocess.run(list(args), check=True, capture_output=True, text=True,
                          encoding='utf-8', timeout=180).stdout.strip()


def verify_target(settings, client, container):
    parsed = parse_uri(settings.mongo_uri)
    if (parsed['nodelist'] != [('127.0.0.1', 27018)]
            or settings.mongo_database != 'etl_content'
            or settings.mongo_metadata_mode != 'shared'):
        raise ValueError('BLOCKED_UNEXPECTED_TARGET')
    context = command('docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}')
    if not context.startswith('npipe:'):
        raise ValueError('BLOCKED_NONLOCAL_DOCKER')
    info = json.loads(command('docker', 'inspect', container))[0]
    labels = info['Config'].get('Labels', {})
    repository = Path(__file__).resolve().parents[1]
    if (labels.get('com.docker.compose.service') != 'mongo'
            or labels.get('com.docker.compose.project') != 'etl-master-data'
            or Path(labels.get('com.docker.compose.project.working_dir', '')).resolve() != repository
            or info['HostConfig']['PortBindings'] != {
                '27017/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '27018'}]}):
        raise ValueError('BLOCKED_CONTAINER_IDENTITY')
    server = client.admin.command('serverStatus')
    hello = client.admin.command('hello')
    if (server['host'] != info['Config']['Hostname']
            or hello.get('setName') or hello.get('msg') == 'isdbgrid'):
        raise ValueError('BLOCKED_SERVER_IDENTITY')
    return {'environment': 'local-compose-development', 'host': '127.0.0.1:27018',
            'database': settings.mongo_database, 'container_id': info['Id'],
            'server_host': server['host'], 'compose_project': labels['com.docker.compose.project'],
            'server_version': client.server_info()['version']}


def snapshot(database):
    """Full BSON document digests, counts and metadata; never exports document contents."""
    result = {}
    for entry in sorted(database.list_collections(), key=lambda row: row['name']):
        name = entry['name']
        if entry.get('type') != 'collection':
            raise ValueError('BLOCKED_NON_COLLECTION')
        digest = hashlib.sha256()
        count = 0
        for document in database[name].find({}).sort('_id', 1):
            digest.update(BSON.encode(document))
            count += 1
        indexes = [{k: v for k, v in index.items() if k != 'ns'}
                   for index in database[name].list_indexes()]
        result[name] = {'documents': count, 'documents_sha256': digest.hexdigest(),
                        'options': entry.get('options', {}), 'indexes': indexes}
    return result


def plan(database, dimension):
    checked = check_schema(database, dimension=dimension, audit_documents=True)
    catalog = {row['name']: row for row in database.list_collections()}
    validators = contracts(dimension)
    blocked = []
    if checked['incompatible_collections']:
        blocked.append('INCOMPATIBLE_COLLECTION')
    if checked['incompatible_indexes'] or checked['duplicate_indexes']:
        blocked.append('INCOMPATIBLE_OR_DUPLICATE_INDEX')
    if any(checked['legacy_invalid'].values()):
        blocked.append('INVALID_EXISTING_DOCUMENTS')
    if checked['validation_mismatches']:
        blocked.append('VALIDATION_OPTIONS_MISMATCH')
    operations = []
    for name, validator in validators.items():
        if name not in catalog:
            operations.append({'operation': 'create_collection', 'collection': name,
                               'validator_sha256': fingerprint(validator),
                               'validationLevel': 'strict', 'validationAction': 'error'})
        else:
            installed = catalog[name].get('options', {}).get('validator', {})
            if installed and installed != validator:
                blocked.append('EXISTING_VALIDATOR_REQUIRES_SEPARATE_REVIEW:' + name)
            elif not installed:
                operations.append({'operation': 'collMod', 'collection': name,
                                   'validator_sha256': fingerprint(validator),
                                   'validationLevel': 'strict', 'validationAction': 'error'})
    for item in checked['missing_indexes']:
        index = next(i for i in INDEXES[item['collection']] if i.name == item['name'])
        operations.append({'operation': 'create_index', 'collection': item['collection'],
                           'name': index.name, 'keys': list(index.keys), 'unique': index.unique})
    result = {'database': database.name, 'dimension': dimension,
              'status': 'BLOCKED' if blocked else 'READY_FOR_APPROVAL' if operations else 'NO_CHANGES',
              'blockers': blocked, 'operations': operations,
              'expected_schema_sha256': fingerprint(validators),
              'before_catalog': {name: {'options': row.get('options', {}),
                                      'indexes': list(database[name].list_indexes())}
                                 for name, row in catalog.items()},
              'schema_check': checked}
    result['plan_sha256'] = fingerprint(result)
    return result


def rehearse(database, dimension, expected_snapshot):
    if not database.name.startswith('etl_restore_verify_') or len(database.name) != 51:
        raise ValueError('BLOCKED_NOT_ISOLATED_RESTORE_DATABASE')
    before = snapshot(database)
    if before != expected_snapshot:
        raise ValueError('BLOCKED_RESTORE_VERIFICATION')
    current_plan = plan(database, dimension)
    if current_plan['blockers']:
        raise ValueError('BLOCKED_REHEARSAL_PREFLIGHT')
    # The only installer write in this script is guarded by the isolated DB prefix.
    checked = initialize(database, dimension=dimension, apply=True)
    after = snapshot(database)
    for name, entry in before.items():
        if (after[name]['documents'], after[name]['documents_sha256']) != (
                entry['documents'], entry['documents_sha256']):
            raise ValueError('BLOCKED_REHEARSAL_DATA_CHANGED')
        if name not in contracts(dimension) and after[name] != entry:
            raise ValueError('BLOCKED_EXTRA_COLLECTION_CHANGED')
    if not checked['schema_ready'] or plan(database, dimension)['status'] != 'NO_CHANGES':
        raise ValueError('BLOCKED_REHEARSAL_POSTCHECK')
    return {'schema_ready': True, 'documents_unchanged': True, 'idempotent_plan': 'NO_CHANGES'}


def verify_application_read(database):
    database = database.with_options(codec_options=CodecOptions(tz_aware=True, tzinfo=UTC))
    counts = {}
    for name, model in zip(INDEXES, (UserProfileDocument, ResourceDescriptionDocument,
                                    OCRPageDocument, ContentUnitDocument, ContentBagDocument), strict=True):
        counts[name] = 0
        for document in database[name].find({}):
            model.model_validate(document)
            counts[name] += 1
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='etl-master-data-mongo-1')
    parser.add_argument('--backup-and-verify', action='store_true')
    parser.add_argument('--output-dir', type=Path)
    args = parser.parse_args(argv)
    client = None
    phase = 'VERIFY_TARGET'
    try:
        settings = Settings()
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000, timeoutMS=30000)
        identity = verify_target(settings, client, args.container)
        database = client[settings.mongo_database]
        current_plan = plan(database, settings.index_embedding_dimension)
        if not args.backup_and_verify:
            print(json.dumps({'identity': identity, 'plan': current_plan}, default=str))
            return 1 if current_plan['blockers'] else 0
        if current_plan['blockers']:
            raise ValueError('BLOCKED_PREFLIGHT')
        if settings.index_embedding_dimension != 384:
            raise ValueError('BLOCKED_MODEL_DIMENSION')
        if not args.output_dir:
            raise ValueError('BLOCKED_BACKUP_DIRECTORY_REQUIRED')
        output = args.output_dir.resolve()
        repository = Path(__file__).resolve().parents[1]
        if output.is_relative_to(repository) or not output.is_dir() or any(output.iterdir()):
            raise ValueError('BLOCKED_BACKUP_DIRECTORY_NOT_PRIVATE_EMPTY_EXTERNAL')
        manifest = {'created_at': datetime.now(UTC).isoformat(), 'identity': identity,
                    'plan': current_plan, 'dev_schema_executed': False,
                    'operator_dev_write_approval': 'PENDING'}
        phase = 'BACKUP'
        before = snapshot(database)
        archive_in_container = '/tmp/etl-backup-' + uuid4().hex + '.archive.gz'
        archive = output / 'etl_content.archive.gz'
        command('docker', 'exec', args.container, 'mongodump', '--host=127.0.0.1',
                '--port=27017', '--db=etl_content', '--gzip', '--numParallelCollections=1',
                '--archive=' + archive_in_container)
        command('docker', 'cp', args.container + ':' + archive_in_container, str(archive))
        if snapshot(database) != before:
            raise ValueError('BLOCKED_WRITES_DURING_BACKUP')
        manifest['archive_sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        manifest['before'] = before
        phase = 'RESTORE_VERIFICATION'
        restored_name = 'etl_restore_verify_' + uuid4().hex
        if restored_name in client.list_database_names():
            raise ValueError('BLOCKED_RESTORE_TARGET_EXISTS')
        command('docker', 'exec', args.container, 'mongorestore', '--host=127.0.0.1',
                '--port=27017', '--gzip', '--archive=' + archive_in_container,
                '--nsInclude=etl_content.*', '--nsFrom=etl_content.*',
                '--nsTo=' + restored_name + '.*', '--stopOnError')
        restored = client[restored_name]
        if snapshot(restored) != before:
            raise ValueError('BLOCKED_RESTORE_CONTENT_OR_METADATA_MISMATCH')
        manifest['restore_verification'] = {'database': restored_name, 'verified': True,
                                            'full_bson_and_metadata_match': True,
                                            'application_read_counts': verify_application_read(restored)}
        phase = 'ISOLATED_MIGRATION_REHEARSAL'
        manifest['rehearsal'] = rehearse(restored, settings.index_embedding_dimension, before)
        if snapshot(database) != before or plan(database, settings.index_embedding_dimension) != current_plan:
            raise ValueError('BLOCKED_DEV_CHANGED_DURING_VERIFICATION')
        manifest['dev_unchanged'] = True
        (output / 'manifest.json').write_text(json.dumps(manifest, default=str, indent=2), encoding='utf-8')
        print(json.dumps({'backup_directory': str(output), 'archive_sha256': manifest['archive_sha256'],
                          'plan_sha256': current_plan['plan_sha256'],
                          'restore_verification': manifest['restore_verification'],
                          'rehearsal': manifest['rehearsal'], 'dev_schema_executed': False,
                          'approval': 'PENDING'}))
        return 0
    except Exception as exc:
        # Never emit arbitrary subprocess output/connection exceptions or credentials.
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('BLOCKED_') else type(exc).__name__
        print(json.dumps({'status': 'BLOCKED', 'phase': phase, 'code': code,
                          'dev_schema_executed': False}))
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == '__main__':
    raise SystemExit(main())
