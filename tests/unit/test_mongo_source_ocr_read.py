from types import SimpleNamespace

import pytest
from pymongo.errors import OperationFailure

from data_sync_etl.adapters.mongo.source_ocr_read import MongoExistingSourceOCRReader
from data_sync_etl.domain.core import DomainError


class Database:
    def __init__(self, names):
        self.names = names

    def with_options(self, **_kwargs):
        return self

    def list_collection_names(self, **_kwargs):
        if self.names is None:
            raise OperationFailure('private connection details')
        return self.names


def test_missing_or_unavailable_collection_never_reads_or_creates():
    for names, error in ((['ocr_pages'], 'COLLECTION_MISSING'), (None, 'UNAVAILABLE')):
        adapter = MongoExistingSourceOCRReader(Database(names))
        adapter.reader = SimpleNamespace(ocr_pages=lambda **_: pytest.fail('unexpected content read'))
        with pytest.raises(DomainError, match=error):
            adapter.ocr_pages(version_id='v1', job_id='j1')


def test_existing_reader_checks_unit_version_and_currentness():
    adapter = MongoExistingSourceOCRReader(Database(['ocr_pages', 'mongo_content_units']))
    adapter.reader = SimpleNamespace(get_unit=lambda _: SimpleNamespace(resource_version_id='other', is_current=True))
    with pytest.raises(DomainError, match='UNIT_CONFLICT'):
        adapter.get_unit('u1', version_id='v1')
    adapter.reader = SimpleNamespace(get_unit=lambda _: SimpleNamespace(resource_version_id='v1', is_current=False))
    with pytest.raises(DomainError, match='UNIT_CONFLICT'):
        adapter.get_unit('u1', version_id='v1')
