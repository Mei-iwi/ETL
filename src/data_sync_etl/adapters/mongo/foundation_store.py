"""Typed metadata/read adapters, explicitly assigned IDs, no implicit SOURCE sync."""

import hashlib
import json
from copy import deepcopy
from datetime import UTC

from bson import BSON, ObjectId
from bson.codec_options import CodecOptions
from bson.errors import BSONError
from pydantic import TypeAdapter, ValidationError
from pymongo.errors import DuplicateKeyError, OperationFailure

from data_sync_etl.adapters.mongo.content_store import generation_filter, invalidate_bags
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import (
    ContentBagDocument,
    ContentUnitDocument,
    Document,
    DocumentId,
    Int32,
    OCRPageDocument,
    ResourceDescriptionDocument,
    UserProfileDocument,
    normalize_keywords,
)
from data_sync_etl.ports.search import SEARCH_DOCUMENT_VALIDATION


def _copy_search_bag(document):
    # Validated embedding elements are immutable floats. Copy their lists in C
    # instead of asking deepcopy to visit millions of individual coordinates.
    vectors = [document.embedding]
    if document.meanings is not None:
        vectors.extend(member.embedding for member in document.meanings.members)
    return deepcopy(document, {id(vector): vector.copy() for vector in vectors})


def checked(model, value):
    try:
        if isinstance(value, Document):
            value = value.bson()
        scope, key = SEARCH_DOCUMENT_VALIDATION.get(), None
        if scope is not None and model is ContentBagDocument and isinstance(value, dict):
            try:
                # Compare the FULL freshly read BSON, including unknown fields.
                # Neither IDs nor caller-supplied fingerprints prove equality.
                key = BSON.encode(value)
            except (BSONError, OverflowError):
                pass  # Preserve strict DTO validation for non-BSON test inputs.
            if key is not None and key in scope['documents']:
                return _copy_search_bag(scope['documents'][key])
        document = model.model_validate(value)
        if key is not None and scope['bytes'] + len(key) <= 16 * 1024 * 1024:
            scope['documents'][key] = _copy_search_bag(document)
            scope['bytes'] += len(key)
        return document
    except ValidationError:
        raise DomainError("Invalid Mongo document") from None


class ProfileConflict(DomainError):
    pass


def user_profile_revision(document):
    if document is None:
        return 'missing'
    value = json.dumps(document.model_dump(mode='json', by_alias=True, exclude_unset=True),
                       sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def identifier(value):
    try:
        return TypeAdapter(DocumentId).validate_python(value)
    except ValidationError:
        raise DomainError("Invalid document identifier") from None


def bounds(limit, offset=0):
    if (
        type(limit) is not int
        or not 1 <= limit <= 100
        or type(offset) is not int
        or not 0 <= offset <= 10000
    ):
        raise DomainError("Mongo read bounds exceeded")


def page(value):
    try:
        return TypeAdapter(Int32).validate_python(value)
    except ValidationError:
        raise DomainError("Invalid page number") from None


def keywords_query(keywords):
    try:
        normalized = normalize_keywords(keywords)
    except ValueError:
        raise DomainError("Invalid keyword query") from None
    if not normalized or len(normalized) > 100 or any(len(v) > 200 for v in normalized):
        raise DomainError("Keyword query bounds exceeded")
    return normalized


def utc_database(database):
    return database.with_options(codec_options=CodecOptions(tz_aware=True, tzinfo=UTC))


class MongoMetadataStore:
    def __init__(self, database):
        self.database = utc_database(database)

    def _upsert(self, collection, model, document):
        data = checked(model, document).bson()
        values = {k: v for k, v in data.items() if k not in ("_id", "created_at")}
        # A document DTO is a complete replacement of optional extension fields.
        unset = {
            k: ""
            for k in model.model_fields
            if k not in ("id", "created_at", "updated_at") and k not in data
        }
        update = {"$set": values, "$setOnInsert": {"created_at": data["created_at"]}}
        if unset:
            update["$unset"] = unset
        self.database[collection].update_one({"_id": data["_id"]}, update, upsert=True)

    def _get(self, collection, model, document_id):
        document_id = identifier(document_id)
        if ObjectId.is_valid(document_id) and self.database[collection].find_one(
            {'_id': ObjectId(document_id)}, {'_id': 1},
        ) is not None:
            raise DomainError('BLOCKED_MONGO_ID_MAPPING')
        # Whitelist avoids returning any legacy authentication fields.
        fields = {f.alias or name: 1 for name, f in model.model_fields.items()}
        result = self.database[collection].find_one({"_id": document_id}, fields)
        return checked(model, result) if result is not None else None

    def upsert_user(self, document: UserProfileDocument):
        self._upsert("users", UserProfileDocument, document)

    def get_user(self, document_id):
        return self._get("users", UserProfileDocument, document_id)

    def update_personal_profile(self, document_id, changes, revision):
        if not changes or set(changes) - {'organization_name', 'address', 'bio'}:
            raise DomainError('Invalid personal profile fields')
        document_id = identifier(document_id)
        self.get_user(document_id)  # Reject legacy ObjectId collisions before writing.
        collection = self.database.users
        fields = {field.alias or name: 1 for name, field in UserProfileDocument.model_fields.items()}
        raw = collection.find_one({'_id': document_id}, fields)
        current = checked(UserProfileDocument, raw) if raw is not None else None
        if user_profile_revision(current) != revision:
            raise ProfileConflict('Personal profile changed')
        stamp = now()
        candidate = checked(UserProfileDocument, {
            **(current.bson() if current else {'_id': document_id, 'created_at': stamp}),
            **changes, 'updated_at': stamp})
        if raw is None:
            try:
                collection.insert_one(candidate.bson())
            except DuplicateKeyError:
                raise ProfileConflict('Personal profile changed') from None
        else:
            conditions = [{key: {'$exists': False}} if key not in raw else
                          {key: {'$exists': True, '$eq': raw[key]}} for key in fields if key != '_id']
            result = collection.update_one({'_id': document_id, '$and': conditions},
                {'$set': {**changes, 'updated_at': stamp}})
            if not result.matched_count:
                raise ProfileConflict('Personal profile changed')
        return self.get_user(document_id)

    def upsert_resource(self, document: ResourceDescriptionDocument):
        self._upsert("learning_resources", ResourceDescriptionDocument, document)

    def get_resource(self, document_id):
        return self._get("learning_resources", ResourceDescriptionDocument, document_id)

    def update_resource_description(self, document_id, changes, revision):
        if not changes or set(changes) - {'description', 'authors', 'tags'}:
            raise DomainError('Invalid resource description fields')
        document_id = identifier(document_id)
        self.get_resource(document_id)  # Reject ambiguous legacy ObjectId identities.
        collection = self.database.learning_resources
        fields = {field.alias or name: 1 for name, field in ResourceDescriptionDocument.model_fields.items()}
        raw = collection.find_one({'_id': document_id}, fields)
        current = checked(ResourceDescriptionDocument, raw) if raw is not None else None
        if user_profile_revision(current) != revision:
            raise ProfileConflict('Resource description changed')
        stamp = now()
        candidate = checked(ResourceDescriptionDocument, {
            **(current.bson() if current else {'_id': document_id, 'created_at': stamp}),
            **changes, 'updated_at': stamp})
        if raw is None:
            try:
                collection.insert_one(candidate.bson())
            except DuplicateKeyError:
                raise ProfileConflict('Resource description changed') from None
        else:
            conditions = [{key: {'$exists': False}} if key not in raw else
                          {key: {'$exists': True, '$eq': raw[key]}} for key in fields if key != '_id']
            values = candidate.bson()
            result = collection.update_one({'_id': document_id, '$and': conditions},
                {'$set': {key: values[key] for key in (*changes, 'updated_at')}})
            if not result.matched_count:
                raise ProfileConflict('Resource description changed')
        return self.get_resource(document_id)

    def sync_source_metadata(self, stream, document):
        if stream not in ('users', 'resources'):
            raise DomainError('Invalid metadata stream')
        model = UserProfileDocument if stream == 'users' else ResourceDescriptionDocument
        collection = self.database['users' if stream == 'users' else 'learning_resources']
        incoming = checked(model, document).bson()
        # Validate ObjectId collisions and the target contract without copying unknown fields.
        for _ in range(3):
            current = self._get(collection.name, model, incoming['_id'])
            if current is None:
                try:
                    collection.insert_one(incoming)
                    return 'INSERTED'
                except DuplicateKeyError:
                    continue
                except OperationFailure as exc:
                    if exc.code == 121:
                        raise DomainError('MONGO_METADATA_DOCUMENT_INVALID') from None
                    raise
            fields = {field.alias or name: 1 for name, field in model.model_fields.items()}
            raw = collection.find_one({'_id': incoming['_id']}, fields)
            if raw is None:
                continue
            old = checked(model, raw).bson()
            extension = set(model.model_fields) - {'id', 'created_at', 'updated_at'}
            # An identity-only legacy doc has no source-owned metadata yet.
            if old['updated_at'] > incoming['updated_at']:
                return 'STALE'
            changes = {key: value for key, value in incoming.items()
                       if key in extension and (key not in old or old[key] != value)}
            if not changes:
                return 'UNCHANGED'
            changes['updated_at'] = incoming['updated_at']
            # Compare all known fields, including ABSENT vs NULL, to avoid racing another writer.
            # CAS uses BSON as stored, not normalized DTO arrays (e.g. legacy mixed-case tags).
            conditions = [{key: {'$exists': False}} if key not in raw else
                          {key: {'$exists': True, '$eq': raw[key]}}
                          for key in (extension | {'created_at', 'updated_at'})]
            try:
                result = collection.update_one({'_id': incoming['_id'], '$and': conditions}, {'$set': changes})
            except OperationFailure as exc:
                if exc.code == 121:
                    raise DomainError('MONGO_METADATA_DOCUMENT_INVALID') from None
                raise
            if result.matched_count:
                return 'UPDATED'
        raise DomainError('MONGO_METADATA_CONCURRENT_CHANGE')

    def _ensure_identity(self, collection, model, document):
        data = checked(model, document).bson()
        if set(data) != {"_id", "created_at", "updated_at"}:
            raise DomainError("Identity bootstrap accepts only identity fields")
        # A legacy ObjectId with the same textual value is NOT the same identity.
        # Inspect only; never convert/move it or create a second logical document.
        if ObjectId.is_valid(data['_id']) and self.database[collection].find_one(
            {'_id': ObjectId(data['_id'])}, {'_id': 1},
        ) is not None:
            raise DomainError('BLOCKED_MONGO_ID_MAPPING')
        # Insert-only, atomic: never change an existing document, even its dates.
        # Unlike _upsert(), this path cannot unset independently owned extensions.
        try:
            result = self.database[collection].update_one(
                {"_id": data["_id"]}, {"$setOnInsert": data}, upsert=True,
            )
        except DuplicateKeyError:
            # Another initializer won. Read/validate without rewriting its data.
            if self._get(collection, model, data["_id"]) is None:
                raise
            return False
        except OperationFailure as exc:
            if exc.code == 121:
                raise DomainError('MONGO_METADATA_DOCUMENT_INVALID') from None
            raise
        if result.upserted_id is None and self._get(collection, model, data['_id']) is None:
            raise DomainError('MONGO_METADATA_CONCURRENT_CHANGE')
        return result.upserted_id is not None

    def ensure_user_identity(self, document: UserProfileDocument) -> bool:
        return self._ensure_identity("users", UserProfileDocument, document)

    def ensure_resource_identity(self, document: ResourceDescriptionDocument) -> bool:
        return self._ensure_identity("learning_resources", ResourceDescriptionDocument, document)


class MongoContentReader:
    def __init__(self, database):
        self.database = utc_database(database)

    def ocr_pages(self, *, version_id=None, job_id=None, page_num=None, limit=100, offset=0):
        bounds(limit, offset)
        if version_id is None and job_id is None:
            raise DomainError("Job or version filter required")
        selector = {}
        if version_id is not None:
            selector["resource_version_id"] = identifier(version_id)
        if job_id is not None:
            selector["job_id"] = identifier(job_id)
        if page_num is not None:
            selector["page_num"] = page(page_num)
        return [
            checked(OCRPageDocument, doc)
            for doc in self.database.ocr_pages.find(selector)
            .sort([("page_num", 1), ("_id", 1)])
            .skip(offset)
            .limit(limit)
        ]

    def get_unit(self, unit_id):
        doc = self.database.mongo_content_units.find_one({"_id": identifier(unit_id)})
        return checked(ContentUnitDocument, doc) if doc is not None else None

    def units(
        self, version_id, *, page_from=None, page_to=None, is_current=True, limit=100, offset=0
    ):
        bounds(limit, offset)
        if type(is_current) is not bool:
            raise DomainError("Invalid current filter")
        selector = {"resource_version_id": identifier(version_id), "is_current": is_current}
        if page_from is not None:
            selector["page_to"] = {"$gte": page(page_from)}
        if page_to is not None:
            selector["page_from"] = {"$lte": page(page_to)}
        if page_from is not None and page_to is not None and page_from > page_to:
            raise DomainError("Invalid page range")
        return [
            checked(ContentUnitDocument, doc)
            for doc in self.database.mongo_content_units.find(selector)
            .sort([("page_from", 1), ("_id", 1)])
            .skip(offset)
            .limit(limit)
        ]

    def keyword_candidates(self, keywords, *, version_id=None, limit=100):
        bounds(limit)
        selector = {"keywords": {"$in": keywords_query(keywords)}, "is_current": True}
        if version_id is not None:
            selector["resource_version_id"] = identifier(version_id)
        return [
            checked(ContentUnitDocument, doc)
            for doc in self.database.mongo_content_units.find(selector).sort("_id", 1).limit(limit)
        ]

    def update_leaf_keywords(self, document: ContentUnitDocument, keywords: list[str]) -> None:
        """Caller must check leaf membership against PG, then supply the observed text/generation."""
        observed = checked(ContentUnitDocument, document)
        normalized = normalize_keywords(keywords)
        # Validate the leaf limit before invalidating bags or making any writes.
        checked(ContentUnitDocument, {**observed.bson(), 'keywords': normalized})
        invalidate_bags(self.database, observed.resource_version_id, observed.projection_generation)
        selector = {
            "_id": observed.id,
            "resource_version_id": observed.resource_version_id,
            "text": observed.text,
            "is_current": True,
            "projection_generation": observed.projection_generation
            if observed.projection_generation is not None
            else {"$exists": False},
        }
        result = self.database.mongo_content_units.update_one(
            selector, {"$set": {"keywords": normalized, "updated_at": now()}}
        )
        if not result.matched_count:
            raise DomainError("Keyword snapshot changed; retry from current leaf")

    def content_fingerprint(self, version_id, *, generation=None):
        # Bounded infrastructure seam, not an unbounded semantic search scan.
        records = list(
            self.database.mongo_content_units.find(
                {"resource_version_id": identifier(version_id), "is_current": True},
                {"_id": 1, "text": 1, "keywords": 1, "projection_generation": 1},
            )
            .sort("_id", 1)
            .limit(10001)
        )
        if not records or len(records) > 10000:
            raise DomainError("Current content snapshot unavailable or exceeds limit")
        if generation is not None and any(
            row.get("projection_generation") != generation for row in records
        ):
            raise DomainError("Current content generation mismatch")
        value = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MongoBagStore:
    def search_phase(self, version_ids, *, limit, max_chars=10_000_000):
        """One bounded fresh read phase, reused only within the calling Search.

        Full Bag BSON is read before content fingerprints. The final phase must
        be created independently; PostgreSQL snapshots and _verified still read
        live content separately. No timestamp-only or cross-request cache.
        """
        versions = tuple(identifier(value) for value in version_ids)
        if (len(versions) > 10000 or len(set(versions)) != len(versions) or not 1 <= limit <= 10001
                or type(max_chars) is not int or not 1 <= max_chars <= 50_000_000):
            raise DomainError('Search phase bounds exceeded')
        documents, content = {}, {version: [] for version in versions}
        selector = {'resource_version_id': {'$in': list(versions)}, 'is_current': True}
        rows = list(self.database.content_bags.find(selector).sort('_id', 1).limit(limit).max_time_ms(5000))
        if len(rows) >= limit:
            from data_sync_etl.domain.search import SearchError
            raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
        for raw in rows:
            doc = checked(ContentBagDocument, raw)
            if doc.resource_version_id not in content or doc.id in documents:
                raise DomainError('Search phase identity mismatch')
            documents[doc.id] = doc
        # Keep the global read bounded even when many versions contain leaves.
        rows = self.database.mongo_content_units.find(selector, {
            '_id': 1, 'resource_version_id': 1, 'text': 1, 'keywords': 1,
            'projection_generation': 1}).sort('_id', 1).limit(10001).max_time_ms(5000)
        total_chars = 0
        for ordinal, raw in enumerate(rows, 1):
            if not isinstance(raw.get('text'), str):
                raise DomainError('Invalid search content phase text')
            total_chars += len(raw.get('text', ''))
            if ordinal > 10000 or total_chars > max_chars:
                from data_sync_etl.domain.search import SearchError
                raise SearchError('SEARCH_LIMIT_EXCEEDED', 422)
            version = raw.get('resource_version_id')
            if version not in content:
                raise DomainError('Search content phase identity mismatch')
            content[version].append({key: value for key, value in raw.items() if key != 'resource_version_id'})
        fingerprints = {version: hashlib.sha256(json.dumps(records, ensure_ascii=False,
            sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            for version, records in content.items() if records}

        def read(root_ids, *, version_id, generation):
            ids = tuple(identifier(value) for value in root_ids)
            if (version_id not in content or len(ids) > 10000 or len(set(ids)) != len(ids)
                    or type(generation) is not int or generation < 0):
                raise DomainError('Search phase request bounds exceeded')
            records = content[version_id]
            if not records or any(row.get('projection_generation') != generation for row in records):
                return {key: None for key in ids}
            return {key: doc if (doc is not None and doc.resource_version_id == version_id
                and doc.projection_generation == generation
                and doc.content_fingerprint == fingerprints[version_id]
                and len(doc.embedding) == self.dimension) else None
                for key in ids for doc in [documents.get(key)]}
        return read

    def search_bags(self, root_ids, *, version_id, generation):
        """Fresh, version-bounded read phase for Search ONLY; never a cache.

        Read the exact requested Bags, then independently read current content.
        _verified still performs its separate pre-read fingerprint and validates
        all ownership/structure/model/keywords/vector fields. Writers/get_bag
        retain their original per-document verification/timing.
        """
        ids = tuple(identifier(key) for key in root_ids)
        version_id = identifier(version_id)
        if (len(ids) > 10000 or len(set(ids)) != len(ids)
                or type(generation) is not int or generation < 0):
            raise DomainError('Search Bag batch bounds exceeded')
        if not ids:
            return {}
        documents = {}
        for offset in range(0, len(ids), 500):
            batch = ids[offset:offset + 500]
            allowed = set(batch)
            selector = {'_id': {'$in': list(batch)}, 'resource_version_id': version_id, 'is_current': True}
            # get_bag validates the FULL document with extra="forbid". A field
            # projection would hide legacy restriction/unknown fields and weaken
            # that contract, so the strict DTO requires the complete Bag here.
            rows = self.database.content_bags.find(selector).sort('_id', 1).limit(len(batch) + 1)
            for raw in rows:
                doc = checked(ContentBagDocument, raw)
                if doc.id not in allowed or doc.id in documents or doc.resource_version_id != version_id:
                    raise DomainError('Search Bag batch identity mismatch')
                documents[doc.id] = doc
        # AFTER all Bag reads: never trust the earlier snapshot fingerprint as
        # proof that Mongo content stayed unchanged during this phase.
        try:
            fingerprint = self.reader.content_fingerprint(version_id, generation=generation)
        except DomainError:
            return {key: None for key in ids}
        return {key: doc if (doc is not None and doc.is_current
                            and doc.projection_generation == generation
                            and doc.content_fingerprint == fingerprint
                            and len(doc.embedding) == self.dimension) else None
                for key in ids for doc in [documents.get(key)]}

    def search_candidates(self, version_ids, *, model, limit):
        """Read at most cap+1, allowing caller to reject an incomplete global ranking."""
        if not 1 <= limit <= 10001 or len(version_ids) > 10000:
            raise DomainError('Search candidate bounds exceeded')
        selector = {'resource_version_id': {'$in': [identifier(v) for v in version_ids]},
                    'is_current': True, 'embedding_model': model}
        fields = {field.alias or key: 1 for key, field in ContentBagDocument.model_fields.items()}
        return [checked(ContentBagDocument, data) for data in
                self.database.content_bags.find(selector, fields).sort('_id', 1).limit(limit).max_time_ms(5000)]

    def __init__(self, database, dimension: int, *, historical_dimensions=()):
        if type(dimension) is not int or not 1 <= dimension <= 4096:
            raise DomainError("Invalid embedding dimension")
        self.database = utc_database(database)
        self.dimension = dimension
        self.historical_dimensions = tuple(historical_dimensions)
        self.reader = MongoContentReader(database)

    def invalidate_other_roots(self, version_id, root_ids, generation):
        self.database.content_bags.update_many(
            {'resource_version_id': identifier(version_id), '_id': {'$nin': list(root_ids)},
             **generation_filter(generation)},
            {'$set': {'is_current': False, 'updated_at': now()}},
        )

    def ensure_ready(self):
        from data_sync_etl.adapters.mongo.lexical_schema import check_lexical_schema
        from data_sync_etl.adapters.mongo.meaning_schema import check_meanings_schema
        from data_sync_etl.adapters.mongo.schema import check_schema
        from data_sync_etl.domain.indexing import IndexingError

        if (not check_meanings_schema(self.database, dimension=self.dimension, historical_dimensions=self.historical_dimensions)['schema_ready']
                and not check_lexical_schema(self.database, dimension=self.dimension, historical_dimensions=self.historical_dimensions)['schema_ready']
                and not check_schema(self.database, dimension=self.dimension,
                                    historical_dimensions=self.historical_dimensions)['schema_ready']):
            raise IndexingError('BLOCKED_INDEX_SCHEMA', 503)

    def ensure_lexical_ready(self):
        from data_sync_etl.adapters.mongo.lexical_schema import check_lexical_schema
        from data_sync_etl.adapters.mongo.meaning_schema import check_meanings_schema
        from data_sync_etl.domain.indexing import IndexingError

        if (not check_meanings_schema(self.database, dimension=self.dimension, historical_dimensions=self.historical_dimensions)['schema_ready']
                and not check_lexical_schema(self.database, dimension=self.dimension, historical_dimensions=self.historical_dimensions)['schema_ready']):
            raise IndexingError('BLOCKED_INDEX_SCHEMA', 503)

    def ensure_meanings_ready(self):
        from data_sync_etl.adapters.mongo.meaning_schema import check_meanings_schema
        from data_sync_etl.domain.indexing import IndexingError

        if not check_meanings_schema(self.database, dimension=self.dimension, historical_dimensions=self.historical_dimensions)['schema_ready']:
            raise IndexingError('BLOCKED_INDEX_SCHEMA', 503)

    def _current(self, document):
        if (
            not document.is_current
            or not document.content_fingerprint
            or document.projection_generation is None
        ):
            return False
        try:
            return document.content_fingerprint == self.reader.content_fingerprint(
                document.resource_version_id, generation=document.projection_generation
            )
        except DomainError:
            return False

    def upsert_bag(self, document: ContentBagDocument):
        doc = checked(ContentBagDocument, document)
        if (
            len(doc.embedding) != self.dimension
            or doc.projection_generation is None
            or not self._current(doc)
        ):
            raise DomainError("Bag dimension/generation/content snapshot inconsistent")
        data = doc.bson()
        values = {k: v for k, v in data.items() if k not in ("_id", "created_at")}
        values["is_current"] = doc.is_current
        try:
            self.database.content_bags.update_one(
                {"_id": doc.id, **generation_filter(doc.projection_generation)},
                {"$set": values, "$setOnInsert": {"created_at": doc.created_at}},
                upsert=True,
            )
        except DuplicateKeyError:
            raise DomainError("Finalization ownership lost") from None
        # No shared transaction: readers recheck the snapshot, even if projection races this write.
        if not self._current(doc):
            invalidate_bags(self.database, doc.resource_version_id, doc.projection_generation)
            raise DomainError("Bag snapshot changed; indexing pending")

    def get_bag(self, root_id):
        data = self.database.content_bags.find_one({"_id": identifier(root_id), "is_current": True})
        if data is None:
            return None
        doc = checked(ContentBagDocument, data)
        return doc if len(doc.embedding) == self.dimension and self._current(doc) else None

    def bag_candidates(self, keywords, *, version_id=None, limit=100):
        bounds(limit)
        selector = {"keywords": {"$in": keywords_query(keywords)}, "is_current": True}
        if version_id is not None:
            selector["resource_version_id"] = identifier(version_id)
        result = []
        # Bound both database work and returned candidates, including invalidated snapshots.
        for data in self.database.content_bags.find(selector).sort("_id", 1).limit(limit):
            doc = checked(ContentBagDocument, data)
            if len(doc.embedding) == self.dimension and self._current(doc):
                result.append(doc)
        return result
