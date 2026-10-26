"""Read-only Mongo capability and deterministic fixture inventory; no admin probe."""
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event, RLock

from data_sync_etl.domain.corpus_concepts import fingerprint
from data_sync_etl.domain.indexing import digest
from data_sync_etl.ports.concept_corpus import ConceptBlocked


class CorpusReadOnlyAccessGate:
    def __init__(self, verifier: Callable[[], bool] | None = None):
        self._verifier = verifier

    def require(self):
        if self._verifier is None or self._verifier() is not True:
            raise ConceptBlocked('MONGO_READ_ONLY_NOT_VERIFIED')


class ReadOnlyMongoEvidenceReader:
    PROJECTIONS = {
        'learning_resources': {'_id': 1, 'description': 1, 'tags': 1, 'updated_at': 1},
        'mongo_content_units': {'_id': 1, 'content_unit_id': 1, 'resource_version_id': 1,
            'text': 1, 'is_current': 1, 'projection_generation': 1, 'updated_at': 1},
    }

    def __init__(self, finders, gate, *, batch_size=20, max_docs=100, max_bytes=500_000, max_time_ms=1000):
        if not (1 <= batch_size <= 100 and 1 <= max_docs <= 1000 and 1 <= max_bytes <= 10_000_000
                and 1 <= max_time_ms <= 5000):
            raise ValueError('Invalid corpus read budgets')
        gate.require()
        self._finders, self._gate = finders, gate
        self.batch_size, self.max_docs, self.max_bytes, self.max_time_ms = batch_size, max_docs, max_bytes, max_time_ms
        self._close_connection = lambda: None

    @classmethod
    def from_secret_reference(cls, secret_file, database, gate, *, client_factory=None):
        # A flag/URI is not verification. Never even read a secret before the
        # separately supplied trusted attestor has passed; no admin probe.
        gate.require()
        path = Path(secret_file)
        if path.stat().st_size > 4096:
            raise ConceptBlocked('CORPUS_SECRET_INVALID')
        uri = path.read_text(encoding='utf-8').strip()
        if not uri.startswith(('mongodb://', 'mongodb+srv://')):
            raise ConceptBlocked('CORPUS_SECRET_INVALID')
        if client_factory is None:
            from pymongo import MongoClient
            client_factory = MongoClient
        from pymongo.read_concern import ReadConcern
        client = client_factory(uri, maxPoolSize=2, minPoolSize=0, serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000, socketTimeoutMS=3000, waitQueueTimeoutMS=500, appname='etl-corpus-readonly')
        try:
            db = client.get_database(database, read_concern=ReadConcern('majority'))
            reader = cls({name: db[name].find for name in cls.PROJECTIONS}, gate)
            reader._close_connection = client.close
            return reader
        except Exception:
            client.close()
            raise ConceptBlocked('CORPUS_CONNECTION_FAILED') from None

    def close(self):
        self._close_connection()

    def fields(self, resource):
        """Bridge a MASTER-verified envelope to projected Mongo text. No OCR/media."""
        used = 0
        if resource.metadata_approved:
            values = {name: resource.fields.get(name, '') for name in ('title', 'description', 'tags')}
            if resource.mongo_id:
                extension = self.metadata(resource.mongo_id)
                if extension:
                    values.update(description=extension.get('description') or '', tags='; '.join(extension.get('tags') or []))
            for name, text in values.items():
                used += len(text.encode('utf-8'))
                if used > self.max_bytes:
                    raise ConceptBlocked('CORPUS_BYTE_BUDGET')
                if text:
                    yield name, '', text
        if resource.ready:
            for unit in self.units(resource.version_id, resource.approved_unit_ids):
                if (resource.projection_generation is not None
                        and unit.get('projection_generation') != resource.projection_generation):
                    raise ConceptBlocked('CORPUS_GENERATION_MISMATCH')
                text = unit.get('text', '')
                used += len(text.encode('utf-8'))
                if used > self.max_bytes:
                    raise ConceptBlocked('CORPUS_BYTE_BUDGET')
                yield 'unit', unit['_id'], text

    def units(self, version_id, allowed_unit_ids, *, cancel=None):
        self._gate.require()
        cancel = cancel or Event()
        if not allowed_unit_ids or len(allowed_unit_ids) > self.max_docs:
            if allowed_unit_ids:
                raise ConceptBlocked('CORPUS_UNIT_BUDGET')
            return
        selector = {'resource_version_id': version_id, 'is_current': True, '_id': {'$in': sorted(allowed_unit_ids)}}
        cursor = self._finders['mongo_content_units'](selector, projection=self.PROJECTIONS['mongo_content_units'],
            max_time_ms=self.max_time_ms).batch_size(self.batch_size).limit(self.max_docs + 1)
        used = 0
        try:
            for index, document in enumerate(cursor):
                self._gate.require()
                if cancel.is_set():
                    return
                if index >= self.max_docs:
                    raise ConceptBlocked('CORPUS_DOCUMENT_BUDGET')
                if (document.get('_id') not in allowed_unit_ids or document.get('resource_version_id') != version_id
                        or document.get('is_current') is not True):
                    raise ConceptBlocked('CORPUS_IDENTITY_MISMATCH')
                used += len(document.get('text', '').encode('utf-8'))
                if used > self.max_bytes:
                    raise ConceptBlocked('CORPUS_BYTE_BUDGET')
                yield document
        finally:
            cursor.close()

    def metadata(self, mongo_id):
        self._gate.require()
        cursor = self._finders['learning_resources']({'_id': mongo_id},
            projection=self.PROJECTIONS['learning_resources'], max_time_ms=self.max_time_ms).limit(2)
        try:
            result = list(cursor)
            if len(result) > 1 or any(row.get('_id') != mongo_id for row in result):
                raise ConceptBlocked('CORPUS_IDENTITY_MISMATCH')
            if result and len(str(result[0]).encode('utf-8')) > self.max_bytes:
                raise ConceptBlocked('CORPUS_BYTE_BUDGET')
            return result[0] if result else None
        finally:
            cursor.close()


class FixtureCorpus:
    """Synthetic-only complete authority/change inventory. Never use for live ACL."""
    completeness_verified = True
    scope = 'offline_fixture'

    def __init__(self, resources=()):
        self._rows, self._lock, self._sequence = {}, RLock(), 0
        self._token = digest(['offline-fixture-authority-v1'])
        self._review_token = '0' * 64
        for resource in resources:
            self.put(resource)

    def put(self, resource):
        if resource.scope != self.scope:
            raise ConceptBlocked('FIXTURE_SCOPE_REQUIRED')
        with self._lock:
            old = self._rows.get(resource.resource_id)
            if old and resource.revision < old.revision:
                return False
            if old and resource.revision == old.revision and fingerprint(old) != fingerprint(resource):
                raise ConceptBlocked('CONFLICTING_SAME_REVISION')
            if old and fingerprint(old) == fingerprint(resource):
                return False
            self._rows[resource.resource_id] = deepcopy(replace(resource))
            self._sequence += 1
            self._token = digest([self._token, resource.resource_id, fingerprint(resource)])
        return True

    def page(self, after, limit):
        if not 1 <= limit <= 500:
            raise ValueError('Invalid inventory batch')
        with self._lock:
            return [deepcopy(self._rows[key]) for key in sorted(self._rows) if key > after][:limit]

    def current(self, resource_id):
        with self._lock:
            return deepcopy(self._rows.get(resource_id))

    def token(self):
        with self._lock:
            return digest([self._token, self._review_token])

    def set_review_token(self, token):
        with self._lock:
            self._review_token = token
