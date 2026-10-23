"""Explicit bounded READ ONLY local MASTER/Mongo snapshot for offline evaluation.

No SOURCE connection, model inference, schema install, backfill, or API auth grant.
Full extracted text is saved only under ignored .test-tmp, never printed to logs.
"""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from pymongo import MongoClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.persistence.sqlalchemy import SQLAlchemyUnitOfWork
from data_sync_etl.application.content_bags import bag_structure
from data_sync_etl.application.indexing import IndexingService
from data_sync_etl.config import Settings
from data_sync_etl.db.session import sessions
from data_sync_etl.domain.evaluation import checksum
from data_sync_etl.domain.semantic import context_headings


def export(output, limit):
    if not output.resolve().is_relative_to(Path('.test-tmp').resolve()) or not 1 <= limit <= 100:
        raise ValueError('Private .test-tmp output and bounded limit required')
    settings = Settings()
    if (make_url(settings.database_url).host not in ('127.0.0.1', 'localhost')
            or urlsplit(settings.mongo_uri).hostname not in ('127.0.0.1', 'localhost')):
        raise ValueError('Local READ ONLY audit only')
    engine = create_engine(settings.database_url, connect_args={
        'options': '-c default_transaction_read_only=on -c statement_timeout=10000', 'connect_timeout': 5})
    client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000, timeoutMS=10000)
    try:
        with engine.connect() as connection, connection.begin():
            if connection.scalar(text('SHOW transaction_read_only')) != 'on':
                raise ValueError('READ ONLY transaction required')
        db = client[settings.mongo_database]
        bags = MongoBagStore(db, settings.index_embedding_dimension,
                            historical_dimensions=settings.index_historical_embedding_dimensions)
        bags.ensure_ready()
        embedding = LocalSentenceEmbedding(settings.index_embedding_model_path, settings.index_embedding_model_id,
                                          settings.index_embedding_model_version, settings.index_embedding_dimension)
        indexing = IndexingService(SQLAlchemyUnitOfWork(sessions(engine)), MongoContentReader(db),
                                  embedding, settings, bags=bags)
        with indexing.uow() as repo:
            rows = repo.search_versions({}, limit=limit + 1)
            marker = repo.evaluation_state({}, limit=limit, max_units=settings.index_max_units)
            all_versions = repo.find('resource_versions', {}, limit=limit * 5 + 1)
        if len(rows) > limit or len(all_versions) > limit * 5:
            raise ValueError('Audit limit exceeded')
        audit = {'selected_latest_versions': len(rows), 'all_versions': len(all_versions),
                 'version_checks': [], 'collections': db.list_collection_names()}
        for version in all_versions:
            try:
                with indexing.uow() as repo:
                    snapshot = indexing._snapshot(repo, version['id'])
                    runs = indexing._runs(repo, version['id'])
                    if not runs or runs[0]['status'] != 'COMPLETED':
                        raise ValueError('INDEX_NOT_COMPLETED')
                    indexing._verified(snapshot, runs[0])
                code = 'VERIFIED'
            except Exception as exc:
                code = getattr(exc, 'code', 'NOT_VERIFIED')
            audit['version_checks'].append({'version_id': version['id'], 'status': code})
        resources = []
        for row in rows:
            with indexing.uow() as repo:
                snapshot = indexing._snapshot(repo, row['version_id'])
                runs = indexing._runs(repo, row['version_id'])
                indexing._verified(snapshot, runs[0])
                metadata = repo.semantic_metadata(row['resource_id'])
            roots = bag_structure(snapshot[2])
            leaf_root = {leaf: root for root, leaves in roots.items() for leaf in leaves}
            chunks = [{**chunk, 'headings': context_headings(snapshot[2], chunk['content_unit_id']),
                       'bag_id': leaf_root[chunk['content_unit_id']]}
                      for chunk in indexing._preview(snapshot) if chunk['content_unit_id'] in leaf_root]
            records = [bags.get_bag(root) for root in roots]
            resources.append({**row, 'metadata': metadata, 'fingerprint': snapshot[3], 'chunks': chunks,
                'bags': [{'id': bag.id, 'vector': bag.embedding, 'embedding_text': bag.embedding_text}
                         for bag in records if bag is not None]})
        if sum(len(resource['chunks']) for resource in resources) > 2000:
            raise ValueError('Evaluation chunk limit exceeded')
        with indexing.uow() as repo:
            if marker != repo.evaluation_state({}, limit=limit, max_units=settings.index_max_units):
                raise ValueError('Corpus changed during export')
        corpus = {'kind': 'READ_ONLY_LOCAL_SNAPSHOT_NOT_API_AUTHORIZATION', 'resources': resources}
        corpus['checksum'] = checksum(corpus)
        for name in ('content_bags', 'mongo_content_units', 'semantic_chunk_vectors_v1', 'semantic_chunk_indexes_v1'):
            if name in audit['collections']:
                audit[name] = {'count': db[name].count_documents({}, maxTimeMS=5000)}
        audit['bag_models'] = list(db.content_bags.aggregate([
            {'$group': {'_id': {'model': '$embedding_model', 'dimension': {'$size': '$embedding'},
                               'current': '$is_current'}, 'count': {'$sum': 1}}}], maxTimeMS=5000))
        stats = db.command('collStats', 'content_bags')
        audit['legacy_collection_bytes'] = {key: stats.get(key) for key in ('size', 'storageSize', 'totalIndexSize')}
        audit['corpus_checksum'] = corpus['checksum']
        audit['chunks'] = sum(len(resource['chunks']) for resource in resources)
        output.mkdir(parents=True, exist_ok=True)
        for name, data in [('corpus', corpus), ('audit', audit)]:
            (output / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': 'PASS', 'operation': 'readonly_local_snapshot',
            'resources': len(resources), 'chunks': audit['chunks'], 'checksum': corpus['checksum']}))
    finally:
        client.close()
        engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.test-tmp/semantic-quality'))
    parser.add_argument('--limit', type=int, default=100)
    args = parser.parse_args()
    try:
        export(args.output, args.limit)
    except Exception:
        raise SystemExit('READONLY_AUDIT_BLOCKED: verify local configuration/limits; details omitted') from None
