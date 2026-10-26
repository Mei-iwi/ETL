"""Additive v1 metadata collection. Explicit provisioning, never on startup."""

from functools import wraps

from pymongo.errors import CollectionInvalid, PyMongoError

from data_sync_etl.domain.indexing import IndexingError

COLLECTION = 'resource_metadata_indexes_v1'
INDEX = 'metadata_version_unique_v1'
CANDIDATE_INDEX = 'metadata_surface_candidates_v1'
CANDIDATE_KEYS = [('candidate_terms', 1), ('resource_version_id', 1)]
VALIDATOR = {'$jsonSchema': {'bsonType': 'object',
    'required': ['_id', 'resource_id', 'resource_version_id', 'status', 'generation', 'fields',
                 'text', 'lexical', 'projection_fingerprint', 'lexical_fingerprint',
                 'dense_input_fingerprint', 'template', 'updated_at', 'dense_status'],
    'properties': {'_id': {'bsonType': 'string'}, 'resource_id': {'bsonType': 'string'},
                   'resource_version_id': {'bsonType': 'string'},
                   'status': {'enum': ['READY', 'STALE']}, 'generation': {'enum': [1]},
                   'dense_status': {'enum': ['READY', 'NOT_INDEXED', 'FAILED']},
                   'vector': {'bsonType': ['array', 'null'], 'minItems': 1024, 'maxItems': 1024,
                              'items': {'bsonType': 'double'}}}}}


def safe_storage(method):
    @wraps(method)
    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except PyMongoError:
            raise IndexingError('METADATA_STORAGE_UNAVAILABLE') from None
    return call


@safe_storage
def install(database, *, candidates=False):
    if COLLECTION not in database.list_collection_names():
        try:
            database.create_collection(COLLECTION, validator=VALIDATOR,
                                       validationLevel='strict', validationAction='error')
        except CollectionInvalid:
            pass
    store = MongoMetadataIndex(database)
    store.ensure_ready(check_indexes=False)
    database[COLLECTION].create_index([('resource_version_id', 1)], unique=True, name=INDEX)
    if candidates:
        database[COLLECTION].create_index(CANDIDATE_KEYS, name=CANDIDATE_INDEX)
    store.ensure_ready()


class MongoMetadataIndex:
    def __init__(self, database):
        self.database, self.collection = database, database[COLLECTION]

    @safe_storage
    def ensure_candidates_ready(self):
        self.ensure_ready()
        index = self.collection.index_information().get(CANDIDATE_INDEX, {})
        if index.get('key') != CANDIDATE_KEYS:
            raise IndexingError('METADATA_CANDIDATE_INDEX_NOT_READY')

    @safe_storage
    def get_headers(self, version_ids):
        if len(version_ids) > 250:
            raise IndexingError('METADATA_CANDIDATE_LIMIT', 422)
        fields = {name: 1 for name in ('resource_version_id', 'status', 'template', 'generation',
                  'projection_fingerprint', 'lexical_fingerprint', 'dense_input_fingerprint',
                  'candidate_terms', 'candidate_analyzer', 'dense_status', 'model_identity')}
        rows = list(self.collection.find({'resource_version_id': {'$in': version_ids}}, fields).limit(len(version_ids) + 1))
        if len(rows) > len(version_ids) or len({r['resource_version_id'] for r in rows}) != len(rows):
            raise IndexingError('METADATA_SCHEMA_NOT_READY')
        return {row['resource_version_id']: row for row in rows}

    @safe_storage
    def candidates(self, terms, version_ids, *, limit):
        if len(version_ids) > 20000 or len(terms) > 512 or not 1 <= limit <= 2000:
            raise IndexingError('METADATA_CANDIDATE_LIMIT', 422)
        if not terms or not version_ids:
            return []
        rows = list(self.collection.find({'status': 'READY', 'resource_version_id': {'$in': version_ids},
            'candidate_terms': {'$in': terms}}, {'resource_version_id': 1}).hint(CANDIDATE_INDEX)
            .sort('resource_version_id', 1).limit(limit + 1))
        if len(rows) > limit:
            raise IndexingError('METADATA_CANDIDATE_LIMIT', 422)
        return [row['resource_version_id'] for row in rows]

    @safe_storage
    def status_counts(self):
        self.ensure_ready()
        lexical, dense = {}, {}
        for row in self.collection.aggregate([{'$group': {
                '_id': {'status': '$status', 'dense': '$dense_status'}, 'count': {'$sum': 1}}}]):
            state = row['_id'].get('status')
            state = state if state in ('READY', 'STALE') else 'INVALID'
            vector = row['_id'].get('dense')
            vector = vector if vector in ('READY', 'NOT_INDEXED', 'FAILED') else 'INVALID'
            lexical[state] = lexical.get(state, 0) + row['count']
            dense[vector] = dense.get(vector, 0) + row['count']
        return {'lexical': lexical, 'dense': dense, 'scope': 'INDEX_STORAGE_NOT_AUTHORITY_OR_ACL'}

    @safe_storage
    def ensure_ready(self, *, check_indexes=True):
        options = next((row.get('options', {}) for row in self.database.list_collections()
                        if row['name'] == COLLECTION), {})
        if (options.get('validator') != VALIDATOR
                or options.get('validationLevel', 'strict') != 'strict'
                or options.get('validationAction', 'error') != 'error'):
            raise IndexingError('METADATA_SCHEMA_NOT_READY')
        if check_indexes:
            index = self.collection.index_information().get(INDEX, {})
            if not index.get('unique') or index.get('key') != [('resource_version_id', 1)]:
                raise IndexingError('METADATA_SCHEMA_NOT_READY')

    @safe_storage
    def get_many(self, version_ids):
        if len(version_ids) > 2000:
            raise IndexingError('METADATA_CANDIDATE_LIMIT', 422)
        rows = list(self.collection.find({'resource_version_id': {'$in': version_ids},
                                         'status': 'READY'}).limit(len(version_ids) + 1)) if version_ids else []
        if len(rows) > len(version_ids) or len({row['resource_version_id'] for row in rows}) != len(rows):
            raise IndexingError('METADATA_SCHEMA_NOT_READY')
        return {row['resource_version_id']: row for row in rows}

    @safe_storage
    def upsert(self, record):
        self.collection.replace_one({'_id': record['resource_version_id']},
                                    {'_id': record['resource_version_id'], **record}, upsert=True)

    @safe_storage
    def invalidate(self, version_id):
        self.collection.update_one({'_id': version_id}, {'$set': {'status': 'STALE'}})

    @safe_storage
    def page(self, after, *, limit):
        if not 1 <= limit <= 201:
            raise IndexingError('METADATA_CANDIDATE_LIMIT', 422)
        return list(self.collection.find({'_id': {'$gt': after}, 'status': 'READY'},
            {'resource_id': 1, 'resource_version_id': 1}).sort('_id', 1).limit(limit))
