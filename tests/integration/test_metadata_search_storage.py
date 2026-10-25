from datetime import UTC, datetime

import pytest

from data_sync_etl.adapters.mongo.metadata_search import COLLECTION, MongoMetadataIndex, install
from data_sync_etl.domain.indexing import IndexingError


def test_isolated_metadata_schema_unique_dimension(mongo_test_database):
    from pymongo import MongoClient
    from pymongo.errors import DuplicateKeyError, WriteError

    uri, name = mongo_test_database
    with MongoClient(uri) as client:
        database = client[name]
        store = MongoMetadataIndex(database)
        with pytest.raises(IndexingError):
            store.ensure_ready()
        assert COLLECTION not in database.list_collection_names()
        install(database)
        install(database)
        row = dict(resource_id='r', resource_version_id='v', status='READY', generation=1,
            fields={'title': 'safe'}, text='Title: safe', lexical={}, projection_fingerprint='a' * 64,
            lexical_fingerprint='b' * 64, dense_input_fingerprint='c' * 64, template='resource-metadata-v1',
            updated_at=datetime.now(UTC), dense_status='NOT_INDEXED', vector=None)
        store.upsert(row)
        store.upsert(row)
        assert len(store.get_many(['v'])) == 1
        with pytest.raises(DuplicateKeyError):
            database[COLLECTION].insert_one({'_id': 'other', **row})
        with pytest.raises(WriteError):
            database[COLLECTION].insert_one({'_id': 'bad', **row, 'resource_version_id': 'bad', 'vector': [1.] * 384})
        store.invalidate('v')
        assert store.get_many(['v']) == {}


def test_batch_authority_preserves_private_flags_and_legacy_collisions(mongo_test_database):
    from bson import ObjectId
    from pymongo import MongoClient

    from data_sync_etl.adapters.mongo.foundation_store import MongoMetadataStore
    from data_sync_etl.domain.core import DomainError
    uri, name = mongo_test_database
    with MongoClient(uri) as client:
        database = client[name]
        stamp = datetime.now(UTC)
        database.learning_resources.insert_many([
            {'_id': 'public-desc', 'created_at': stamp, 'updated_at': stamp,
             'description': 'Public text', 'tags': ['public'], 'authors': [{'name': 'PRIVATE_AUTHOR'}]},
            {'_id': 'private-desc', 'created_at': stamp, 'updated_at': stamp,
             'description': 'PRIVATE_TEXT', 'description_private': True}])
        reader = MongoMetadataStore(database)
        result = reader.get_resources_search(['public-desc'])
        assert result['public-desc'].description == 'Public text'
        assert result['public-desc'].authors is None and 'PRIVATE' not in str(result)
        with pytest.raises(DomainError, match='METADATA_FIELD_VISIBILITY_UNCONFIRMED'):
            reader.get_resources_search(['private-desc'])
        key = 'a' * 24
        database.learning_resources.insert_many([
            {'_id': key, 'created_at': stamp, 'updated_at': stamp},
            {'_id': ObjectId(key), 'created_at': stamp, 'updated_at': stamp}])
        with pytest.raises(DomainError, match='BLOCKED_MONGO_ID_MAPPING'):
            reader.get_resources_search([key])
