"""Explicit local-only model transition. No startup migration, deletion or SOURCE access."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from bson import BSON, decode_file_iter, json_util
from pymongo import MongoClient
from sqlalchemy import select
from sqlalchemy.engine import make_url

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.schema import check_schema, contracts
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.application.search import SearchService
from data_sync_etl.config import Settings
from data_sync_etl.db.models import EtlStageRun, ResourceVersion
from data_sync_etl.db.session import make_engine, sessions
from data_sync_etl.domain.core import new_id
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search_access import SearchAccess


def local_only(settings):
    if (urlsplit(settings.mongo_uri).hostname not in ('localhost', '127.0.0.1', '::1')
            or make_url(settings.database_url).host not in ('localhost', '127.0.0.1', '::1')):
        raise ValueError('LOCAL_DATABASES_REQUIRED')
    if settings.index_auto_enabled or settings.index_embedding_dimension == 384:
        raise ValueError('TARGET_MODEL_AND_DISABLED_AUTO_INDEX_REQUIRED')
    if settings.index_historical_embedding_dimensions != [384]:
        raise ValueError('HISTORICAL_384_CONFIGURATION_REQUIRED')


def write_json(path, value):
    path.write_text(json_util.dumps(value, indent=2), encoding='utf-8')


def backup_stream(path, records):
    count = 0
    with path.open('xb') as output:
        for record in records:
            output.write(BSON.encode(dict(record)))
            count += 1
        output.flush()
        os.fsync(output.fileno())
    with path.open('rb') as source:
        if sum(1 for _ in decode_file_iter(source)) != count:
            raise ValueError('BACKUP_VERIFICATION_FAILED')
    with path.open('rb') as source:
        checksum = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'file': path.name, 'documents': count, 'sha256': checksum}


def transition_validator(database, dimension):
    # Only the reviewed embedding contract may change; unrelated drift aborts.
    before = check_schema(database, dimension=384, audit_documents=True)
    if not before['schema_ready']:
        raise ValueError('ORIGINAL_SCHEMA_NOT_READY')
    validator = contracts(dimension, historical_dimensions=[384])['content_bags']
    database.command('collMod', 'content_bags', validator=validator,
                     validationLevel='strict', validationAction='error')
    database.content_bags.update_many({}, {'$set': {'is_current': False}})
    checked = check_schema(database, dimension=dimension, historical_dimensions=[384], audit_documents=True)
    if not checked['schema_ready']:
        raise ValueError('TRANSITION_SCHEMA_NOT_READY')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-local-maintenance', action='store_true')
    parser.add_argument('--backup-directory', type=Path)
    args = parser.parse_args()
    settings = Settings()
    local_only(settings)
    engine = make_engine(settings.database_url)
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000)
    database = client[settings.mongo_database]
    uow = SQLAlchemyUnitOfWork(sessions(engine))
    reader = MongoContentReader(database)
    bags = MongoBagStore(database, settings.index_embedding_dimension, historical_dimensions=[384])
    embedding = LocalSentenceEmbedding(settings.index_embedding_model_path, settings.index_embedding_model_id,
        settings.index_embedding_model_version, settings.index_embedding_dimension)
    indexing = IndexingService(uow, reader, embedding, settings, bags=bags)
    report = {'phase': 'PLAN', 'target_model': settings.index_embedding_model_id,
        'dimension': settings.index_embedding_dimension, 'eligible_versions': [], 'skipped': [], 'results': []}
    try:
        if not check_schema(database, dimension=384, audit_documents=True)['schema_ready']:
            raise ValueError('ORIGINAL_SCHEMA_NOT_READY')
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                versions = list(connection.scalars(select(ResourceVersion.id)))
                pending = list(connection.scalars(select(EtlStageRun.id).where(
                    EtlStageRun.stage == 'INDEX_BUILD', EtlStageRun.status.in_(['PENDING', 'RUNNING']))))
        if pending:
            raise ValueError('INFLIGHT_INDEX_RUNS_REQUIRE_REVIEW')
        for version in versions:
            try:
                with uow() as repo:
                    snapshot = indexing._snapshot(repo, version)
                    indexing._roots(snapshot)
                report['eligible_versions'].append(version)
            except IndexingError as error:
                report['skipped'].append({'version': version, 'code': error.code})
        report['old_bags'] = database.content_bags.count_documents({})
        if not args.apply:
            print(json.dumps({'phase': 'PLAN', 'eligible_versions': len(report['eligible_versions']),
                'skipped_versions': len(report['skipped']), 'old_bags': report['old_bags']}))
            return 0
        if not args.confirm_local_maintenance or args.backup_directory is None:
            raise ValueError('EXPLICIT_MAINTENANCE_AND_BACKUP_REQUIRED')
        destination = args.backup_directory.resolve()
        workspace = Path(__file__).resolve().parents[1]
        if destination == workspace or destination.is_relative_to(workspace):
            raise ValueError('PRIVATE_BACKUP_OUTSIDE_WORKSPACE_REQUIRED')
        destination.mkdir(parents=True, exist_ok=False, mode=0o700)
        report_path = destination / 'transition-report.json'
        write_json(report_path, report)
        catalog = list(database.list_collections())
        write_json(destination / 'original-catalog.json', catalog)
        backups = []
        for name in contracts(384):
            backups.append(backup_stream(destination / (name + '.bson'), database[name].find({}).sort('_id', 1)))
        with engine.connect() as connection:
            with connection.begin():
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
                backups.append(backup_stream(destination / 'master-etl-stage-runs.bson',
                    connection.execute(select(EtlStageRun.__table__)).mappings()))
        write_json(destination / 'backup-manifest.json', backups)
        embedding.prepare()  # Model must be usable before the first database write.
        archives = []
        # Read the fixed backup, never a live cursor that could see new archives.
        with (destination / 'content_bags.bson').open('rb') as source:
            for record in decode_file_iter(source):
                archive = {**record, '_id': new_id(), 'is_current': False}
                archives.append({'original_id': record['_id'], 'archive_id': archive['_id'],
                    'sha256_original_bson': hashlib.sha256(BSON.encode(record)).hexdigest()})
                write_json(destination / 'archive-map.json', archives)
                # Never overwrite original IDs or add collections/fields.
                database.content_bags.insert_one(archive)
        report.update(phase='ARCHIVED', archived=len(archives))
        write_json(report_path, report)
        transition_validator(database, settings.index_embedding_dimension)
        report['phase'] = 'VALIDATOR_UPDATED'
        write_json(report_path, report)
        for version in report['eligible_versions']:
            try:
                queued = indexing.enqueue(version, force=True)
                indexing.once()
                with uow() as repo:
                    stage = repo.get('etl_stage_runs', queued['index_run_id'])
                report['results'].append({'version': version, 'status': stage['status'], 'code': stage['error_message']})
            except IndexingError as error:
                report['results'].append({'version': version, 'status': 'FAILED', 'code': error.code})
            write_json(report_path, report)
        search = SearchService(indexing, settings, bags)
        current = database.content_bags.find_one({'is_current': True})
        report['search_checks'] = {}
        if current:
            query = ' '.join(current['keywords'][:3])
            for method in ['bow', 'vector', 'hybrid']:
                result = search.search(query, method, access=SearchAccess(scope='authenticated_admin'))
                report['search_checks'][method] = {'total': result['total'], 'message': result['message']}
        report['phase'] = 'COMPLETE'
        report['schema_ready'] = check_schema(database, dimension=settings.index_embedding_dimension,
            historical_dimensions=[384], audit_documents=True)['schema_ready']
        report['current_legacy_vectors'] = database.content_bags.count_documents(
            {'is_current': True, '$expr': {'$eq': [{'$size': '$embedding'}, 384]}})
        write_json(report_path, report)
        failed = sum(item['status'] != 'COMPLETED' for item in report['results'])
        print(json.dumps({'phase': report['phase'], 'archived': len(archives),
            'completed': len(report['results']) - failed, 'failed': failed,
            'schema_ready': report['schema_ready'], 'search_checks': report['search_checks']}))
        return int(bool(failed or not report['schema_ready'] or not current))
    finally:
        client.close()
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
