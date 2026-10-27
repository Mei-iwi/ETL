"""Read-only Mongo capability and deterministic fixture inventory; no admin probe."""
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from threading import Event, RLock
from time import time
from types import MappingProxyType

from bson import BSON

from data_sync_etl.domain.concept_authority import REQUIRED_COVERAGE, AuthorityAttestation
from data_sync_etl.domain.concept_read_evidence import ReadEvidenceGate
from data_sync_etl.domain.corpus_concepts import eligible, fingerprint
from data_sync_etl.domain.indexing import digest
from data_sync_etl.ports.concept_corpus import ConceptBlocked


class CorpusReadOnlyAccessGate:
    def __init__(self, verifier: Callable[[], bool] | None = None):
        self._verifier = verifier

    def require(self):
        try:
            verified = self._verifier is not None and self._verifier() is True
        except Exception:
            verified = False
        if not verified:
            raise ConceptBlocked('MONGO_READ_ONLY_NOT_VERIFIED')


class CorpusQueryPlanGate:
    """Trusted injected operator attestor, not a flag or self-reported role name.

    Must bind the configured database/principal, selector shape and budget to
    independently verified index/query evidence. No live verifier is installed.
    Synthetic tests may supply a fake; that does not verify a production plan.
    """
    def __init__(self, verifier: Callable[[str], bool] | None = None):
        self._verifier = verifier

    def require(self, collection):
        try:
            verified = self._verifier is not None and self._verifier(collection) is True
        except Exception:
            verified = False
        if not verified:
            raise ConceptBlocked('CORPUS_QUERY_PLAN_NOT_VERIFIED')


class ReadOnlyMongoEvidenceReader:
    PROJECTIONS = MappingProxyType({
        'learning_resources': MappingProxyType({'_id': 1, 'description': 1, 'tags': 1, 'updated_at': 1}),
        'mongo_content_units': MappingProxyType({'_id': 1, 'content_unit_id': 1, 'resource_version_id': 1,
            'text': 1, 'is_current': 1, 'projection_generation': 1, 'updated_at': 1}),
    })

    def __init__(self, finders, gate, *, batch_size=20, max_docs=100, max_bytes=500_000, max_time_ms=1000,
                 query_gate=None):
        if not (1 <= batch_size <= 100 and 1 <= max_docs <= 1000 and 1 <= max_bytes <= 10_000_000
                and 1 <= max_time_ms <= 5000):
            raise ValueError('Invalid corpus read budgets')
        gate.require()
        if not finders or set(finders) - self.PROJECTIONS.keys() or not all(callable(item) for item in finders.values()):
            raise ConceptBlocked('CORPUS_CAPABILITY_INVALID')
        self._finders, self._gate = dict(finders), gate
        self._query_gate = query_gate or CorpusQueryPlanGate()
        self.batch_size, self.max_docs, self.max_bytes, self.max_time_ms = batch_size, max_docs, max_bytes, max_time_ms
        self._close_connection = lambda: None

    @classmethod
    def from_secret_reference(cls, secret_file, database, gate, *, client_factory=None, query_gate=None,
                              read_evidence=None):
        # A flag/URI is not verification. Never even read a secret before the
        # separately supplied trusted attestor has passed; no admin probe.
        gate.require()
        query_gate = query_gate or CorpusQueryPlanGate()
        for name in cls.PROJECTIONS:
            query_gate.require(name)
        if client_factory is None or read_evidence is not None:
            (read_evidence or ReadEvidenceGate()).require(database, secret_file)
        path = Path(secret_file)
        if path.stat().st_size > 4096:
            raise ConceptBlocked('CORPUS_SECRET_INVALID')
        uri = path.read_text(encoding='utf-8').strip()
        if not uri.startswith(('mongodb://', 'mongodb+srv://')):
            raise ConceptBlocked('CORPUS_SECRET_INVALID')
        if read_evidence is not None:
            read_evidence.verify_secret(uri)
        if client_factory is None:
            from pymongo import MongoClient
            client_factory = MongoClient
        from pymongo.read_concern import ReadConcern
        client = client_factory(uri, maxPoolSize=2, minPoolSize=0, serverSelectionTimeoutMS=3000,
            connectTimeoutMS=3000, socketTimeoutMS=3000, waitQueueTimeoutMS=500, appname='etl-corpus-readonly')
        try:
            db = client.get_database(database, read_concern=ReadConcern('majority'))
            class BoundGate:
                def require(self):
                    gate.require()
                    if read_evidence is not None:
                        read_evidence.require(database, secret_file)
            reader = cls({name: db[name].find for name in cls.PROJECTIONS}, BoundGate(), query_gate=query_gate)
            reader._close_connection = client.close
            return reader
        except Exception:
            client.close()
            raise ConceptBlocked('CORPUS_CONNECTION_FAILED') from None

    def close(self):
        self._close_connection()

    def _cursor(self, collection, selector, *, batch, limit):
        cursor = None
        try:
            cursor = self._finders[collection](selector, projection=dict(self.PROJECTIONS[collection]),
                max_time_ms=self.max_time_ms)
            return cursor.batch_size(batch).limit(limit)
        except Exception:
            if cursor is not None:
                cursor.close()
            raise ConceptBlocked('CORPUS_SOURCE_UNAVAILABLE') from None

    def fields(self, resource):
        """Bridge a MASTER-verified envelope to projected Mongo text. No OCR/media."""
        if not eligible(resource):
            return
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
        self._identity(version_id)
        cancel = cancel or Event()
        if not allowed_unit_ids or len(allowed_unit_ids) > self.max_docs:
            if allowed_unit_ids:
                raise ConceptBlocked('CORPUS_UNIT_BUDGET')
            return
        for unit_id in allowed_unit_ids:
            self._identity(unit_id)
        self._query_gate.require('mongo_content_units')
        selector = {'resource_version_id': version_id, 'is_current': True, '_id': {'$in': sorted(allowed_unit_ids)}}
        cursor = self._cursor('mongo_content_units', selector, batch=self.batch_size, limit=self.max_docs + 1)
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
                used += self._document_size('mongo_content_units', document)
                if used > self.max_bytes:
                    raise ConceptBlocked('CORPUS_BYTE_BUDGET')
                yield document
        except ConceptBlocked:
            raise
        except Exception:
            raise ConceptBlocked('CORPUS_SOURCE_UNAVAILABLE') from None
        finally:
            cursor.close()

    def metadata(self, mongo_id):
        self._gate.require()
        self._identity(mongo_id)
        self._query_gate.require('learning_resources')
        cursor = self._cursor('learning_resources', {'_id': mongo_id}, batch=2, limit=2)
        try:
            result = None
            for row in cursor:
                self._gate.require()
                if result is not None or row.get('_id') != mongo_id:
                    raise ConceptBlocked('CORPUS_IDENTITY_MISMATCH')
                if self._document_size('learning_resources', row) > self.max_bytes:
                    raise ConceptBlocked('CORPUS_BYTE_BUDGET')
                result = row
            return result
        except ConceptBlocked:
            raise
        except Exception:
            raise ConceptBlocked('CORPUS_SOURCE_UNAVAILABLE') from None
        finally:
            cursor.close()

    @staticmethod
    def _identity(value):
        if not isinstance(value, str) or not value.strip() or len(value) > 128 or '\x00' in value:
            raise ConceptBlocked('CORPUS_IDENTITY_INVALID')

    @classmethod
    def _document_size(cls, collection, document):
        if not isinstance(document, dict) or set(document) - cls.PROJECTIONS[collection].keys():
            raise ConceptBlocked('CORPUS_PROJECTION_INVALID')
        for field in ('description', 'text'):
            if document.get(field) is not None and not isinstance(document[field], str):
                raise ConceptBlocked('CORPUS_PROJECTION_INVALID')
        tags = document.get('tags')
        if tags is not None and (not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)):
            raise ConceptBlocked('CORPUS_PROJECTION_INVALID')
        try:
            return len(BSON.encode(document))
        except Exception:
            raise ConceptBlocked('CORPUS_PROJECTION_INVALID') from None


class FixtureCorpus:
    """Synthetic-only complete authority/change inventory. Never use for live ACL."""
    completeness_verified = True
    scope = 'offline_fixture'

    def __init__(self, resources=()):
        self._rows, self._lock, self._sequence = {}, RLock(), 0
        self._token = digest(['offline-fixture-authority-v1'])
        self._review_token = '0' * 64
        self._available = True
        self._review_authority = lambda: 'synthetic-review-only'
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
            return digest([self._token, self._review_token, self._review_authority()])

    def bind_review_authority(self, token_provider):
        with self._lock:
            self._review_authority = token_provider

    def set_available(self, available):
        with self._lock:
            if self._available != available:
                self._available = available
                self._token = digest([self._token, 'availability', available])

    def attestation(self):
        return AuthorityAttestation(self.token(), REQUIRED_COVERAGE, time() + 60,
            complete=self.completeness_verified, available=self._available)

    def set_review_token(self, token):
        with self._lock:
            self._review_token = token
