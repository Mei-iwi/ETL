"""Read only the pre-existing OCR collections; never create or modify them."""
from pymongo.errors import PyMongoError

from data_sync_etl.adapters.mongo.foundation_store import MongoContentReader
from data_sync_etl.domain.core import DomainError


class MongoExistingSourceOCRReader:
    def __init__(self, database):
        self.database = database
        self.reader = MongoContentReader(database)

    def _require_existing(self):
        try:
            names = set(self.database.list_collection_names(
                filter={'name': {'$in': ['ocr_pages', 'mongo_content_units']}}))
        except PyMongoError:
            raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None
        if not {'ocr_pages', 'mongo_content_units'} <= names:
            raise DomainError('SOURCE_MONGO_COLLECTION_MISSING')

    def ocr_pages(self, *, version_id, job_id=None, limit=100, offset=0):
        self._require_existing()
        try:
            return self.reader.ocr_pages(version_id=version_id, job_id=job_id, limit=limit, offset=offset)
        except PyMongoError:
            raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None

    def get_unit(self, unit_id, *, version_id):
        self._require_existing()
        try:
            document = self.reader.get_unit(unit_id)
        except PyMongoError:
            raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None
        if document is not None and (document.resource_version_id != version_id or not document.is_current):
            raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
        return document
