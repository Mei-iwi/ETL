"""Read only the pre-existing OCR collections; never create or modify them."""
from pymongo.errors import PyMongoError

from data_sync_etl.adapters.mongo.foundation_store import MongoContentReader, checked
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.mongo_documents import OCRPageDocument


class MongoExistingSourceOCRReader:
    def __init__(self, database):
        self.database = database
        self.reader = MongoContentReader(database)

    def _require_existing(self):
        try:
            # Atlas may reject the $in filter on listCollections even when the
            # principal can list and read the five contract collections. Names
            # are metadata only; list once and test the required subset locally.
            names = set(self.database.list_collection_names())
        except PyMongoError:
            raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None
        if not {'ocr_pages', 'mongo_content_units'} <= names:
            raise DomainError('SOURCE_MONGO_COLLECTION_MISSING')

    def has_ocr_pages(self, version_id):
        """Inventory existence only; never hydrate protected raw text."""
        self._require_existing()
        try:
            return bool(self.database['ocr_pages'].count_documents({'resource_version_id': version_id}, limit=1))
        except PyMongoError:
            raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None

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

    def units(self, version_id, *, limit=100, offset=0):
        """Fresh bounded reads; collection presence and full DTOs stay verified."""
        self._require_existing()
        try:
            documents = self.reader.units(version_id, limit=limit, offset=offset)
        except PyMongoError:
            raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None
        if any(document.resource_version_id != version_id or not document.is_current
               for document in documents):
            raise DomainError('SOURCE_OCR_UNIT_CONFLICT')
        return documents

    def ocr_page(self, *, version_id, job_id, page_num):
        self._require_existing()
        try:
            document = self.reader.database['ocr_pages'].find_one({'_id': f'{job_id}:{page_num}'})
            if document is None:
                return None
            page = checked(OCRPageDocument, document)
            if page.resource_version_id != version_id or page.job_id != job_id or page.page_num != page_num:
                raise DomainError('SOURCE_OCR_PAGE_CONFLICT')
            return page
        except PyMongoError:
            raise DomainError('SOURCE_MONGO_UNAVAILABLE') from None
