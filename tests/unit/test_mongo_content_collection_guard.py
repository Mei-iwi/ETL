from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from pymongo.errors import DuplicateKeyError, OperationFailure

from data_sync_etl.adapters.mongo.content_store import MongoContentStore
from data_sync_etl.domain.core import DomainError


def test_upsert_does_not_create_a_missing_collection():
    database = MagicMock()
    database.list_collection_names.return_value = []
    store = MongoContentStore(database)
    with pytest.raises(DomainError, match='MONGO_COLLECTION_MISSING'):
        store._upsert('ocr_pages', {'_id': 'job:1'})
    database.__getitem__.assert_not_called()


def test_upsert_does_not_write_if_collection_inventory_fails():
    database = MagicMock()
    database.list_collection_names.side_effect = OperationFailure('private server detail')
    store = MongoContentStore(database)
    with pytest.raises(DomainError, match='MONGO_COLLECTION_UNAVAILABLE'):
        store._upsert('ocr_pages', {'_id': 'job:1'})
    database.__getitem__.assert_not_called()


def test_source_page_retries_verify_content_without_overwriting():
    database = MagicMock()
    database.list_collection_names.return_value = ['ocr_pages']
    collection = database.__getitem__.return_value
    timestamp = datetime.now(UTC)
    page = {'_id': 'job:1', 'job_id': 'job', 'page_num': 1, 'resource_version_id': 'v1',
            'source_checksum': 'a' * 64, 'raw_text': '', 'char_count': 0,
            'created_at': timestamp, 'updated_at': timestamp}
    collection.find_one.return_value = dict(page)
    store = MongoContentStore(database)
    assert store.insert_source_ocr_page(page) is True
    collection.insert_one.side_effect = DuplicateKeyError('duplicate')
    assert store.insert_source_ocr_page(page) is False
    collection.find_one.return_value = {**page, 'raw_text': 'different', 'char_count': 9}
    with pytest.raises(DomainError, match='SOURCE_OCR_PAGE_CONFLICT'):
        store.insert_source_ocr_page(page)
    collection.update_one.assert_not_called()
