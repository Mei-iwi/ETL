"""Additive, explicitly installed semantic collections; never touches legacy bags."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import CollectionInvalid, DuplicateKeyError

from data_sync_etl.domain.indexing import IndexingError

VECTORS = 'semantic_chunk_vectors_v1'
INDEXES = 'semantic_chunk_indexes_v1'


def contracts():
    text = {'bsonType': 'string', 'minLength': 1}
    vector = {'bsonType': 'array', 'minItems': 1024, 'maxItems': 1024,
              'items': {'bsonType': 'double'}}
    return {
        INDEXES: {'$jsonSchema': {'bsonType': 'object',
            'required': ['_id', 'status', 'chunk_count', 'updated_at'],
            'properties': {'_id': text, 'status': {'enum': ['PENDING', 'RUNNING', 'READY', 'FAILED']},
                'chunk_count': {'bsonType': 'int', 'minimum': 1}, 'updated_at': {'bsonType': 'date'}}}},
        VECTORS: {'$jsonSchema': {'bsonType': 'object',
            'required': ['_id', 'index_id', 'chunk_id', 'content_unit_id', 'resource_version_id',
                         'source_fingerprint', 'input_hash', 'raw_hash', 'vector', 'dimension',
                         'model_identity', 'strategy', 'status', 'created_at'],
            'properties': {'_id': text, 'index_id': text, 'chunk_id': text,
                'content_unit_id': text, 'resource_version_id': text, 'input_hash': text,
                'raw_hash': text, 'source_fingerprint': text, 'vector': vector,
                'dimension': {'enum': [1024]}, 'status': {'enum': ['READY']},
                'strategy': {'enum': ['raw', 'contextual']}, 'created_at': {'bsonType': 'date'}}}},
    }


def install(database):
    """No collMod, replacement, deletion or auto-install on runtime startup."""
    for name, validator in contracts().items():
        if name not in database.list_collection_names():
            try:
                database.create_collection(name, validator=validator, validationLevel='strict',
                                           validationAction='error')
            except CollectionInvalid:
                pass  # Concurrent explicit installer: check exact contract below.
    store = MongoSemanticStore(database)
    store.ensure_ready(check_indexes=False)
    database[VECTORS].create_index([('index_id', 1), ('chunk_id', 1)], unique=True,
                                   name='semantic_index_chunk')
    database[INDEXES].create_index([('resource_version_id', 1), ('status', 1)],
                                   name='semantic_version_status')
    store.ensure_ready()


class MongoSemanticStore:
    def __init__(self, database):
        self.database = database
        self.indexes, self.vectors = database[INDEXES], database[VECTORS]

    def ensure_ready(self, *, check_indexes=True):
        actual = {row['name']: row for row in self.database.list_collections()}
        for name, validator in contracts().items():
            options = actual.get(name, {}).get('options', {})
            if (options.get('validator') != validator
                    or options.get('validationLevel', 'strict') != 'strict'
                    or options.get('validationAction', 'error') != 'error'):
                raise IndexingError('SEMANTIC_SCHEMA_NOT_READY', 503)
        if check_indexes:
            indexes = self.vectors.index_information()
            index = indexes.get('semantic_index_chunk', {})
            if index.get('key') != [('index_id', 1), ('chunk_id', 1)] or index.get('unique') is not True:
                raise IndexingError('SEMANTIC_SCHEMA_NOT_READY', 503)

    def manifest(self, index_id):
        return self.indexes.find_one({'_id': index_id})

    def has_ready_version(self, version_id):
        return self.indexes.find_one({'resource_version_id': version_id, 'status': 'READY'}, {'_id': 1}) is not None

    def claim(self, plan, lease_seconds):
        now, owner = datetime.now(UTC), uuid4().hex
        try:
            self.indexes.update_one({'_id': plan['_id']}, {'$setOnInsert': {
                **plan, 'status': 'PENDING', 'created_at': now, 'updated_at': now}}, upsert=True)
        except DuplicateKeyError:
            pass
        row = self.indexes.find_one_and_update({'_id': plan['_id'], '$or': [
            {'status': {'$in': ['PENDING', 'FAILED']}},
            {'status': 'RUNNING', 'lease_until': {'$lt': now}}]}, {'$set': {
                'status': 'RUNNING', 'owner': owner, 'updated_at': now,
                'lease_until': now + timedelta(seconds=lease_seconds), 'error_code': None}},
            return_document=ReturnDocument.AFTER)
        if row is None:
            raise IndexingError('SEMANTIC_INDEX_BUSY', 409)
        return owner

    def records(self, index_id, limit):
        return list(self.vectors.find({'index_id': index_id}).sort('chunk_id', 1).limit(limit))

    def save(self, index_id, owner, record):
        if self.indexes.find_one({'_id': index_id, 'status': 'RUNNING', 'owner': owner,
                                  'lease_until': {'$gt': datetime.now(UTC)}}) is None:
            raise IndexingError('SEMANTIC_LEASE_EXPIRED', 409)
        # Immutable row: an old worker cannot overwrite another worker's result.
        self.vectors.update_one({'_id': record['_id']}, {'$setOnInsert': record}, upsert=True)

    def finish(self, index_id, owner, status, code=None):
        result = self.indexes.update_one({'_id': index_id, 'status': 'RUNNING', 'owner': owner,
                                          'lease_until': {'$gt': datetime.now(UTC)}},
            {'$set': {'status': status, 'updated_at': datetime.now(UTC), 'error_code': code}})
        if result.modified_count != 1:
            raise IndexingError('SEMANTIC_LEASE_EXPIRED', 409)
