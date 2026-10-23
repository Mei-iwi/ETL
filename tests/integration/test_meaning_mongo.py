"""Physical validation only in fixture-owned isolated Mongo databases."""

from copy import deepcopy

import pytest
from pymongo import MongoClient
from pymongo.errors import WriteError

from data_sync_etl.adapters.mongo.meaning_schema import check_meanings_schema, install
from data_sync_etl.adapters.mongo.schema import initialize
from data_sync_etl.domain.core import now
from data_sync_etl.domain.lexical import lexical_document
from data_sync_etl.domain.meaning_bags import build_meanings
from data_sync_etl.domain.mongo_documents import ContentBagDocument
from tests.unit.test_meaning_bags import MultilingualDouble


def test_physical_meaning_extension_and_bson_roundtrip(mongo_test_database):
    with MongoClient(mongo_test_database[0], tz_aware=True, serverSelectionTimeoutMS=3000) as client:
        db = client[mongo_test_database[1]]
        initialize(db, apply=True, dimension=3)
        assert not install(db, dimension=3)['ready']
        assert install(db, dimension=3, apply=True)['ready']
        meanings = build_meanings([('u', 'Học máy\nMachine learning')], 'u', None, MultilingualDouble(), .8)
        bag = ContentBagDocument(_id='u', resource_version_id='v', unit_type='TXT_TEXT', keywords=['học'],
                                 embedding_text='học', embedding=[1., 0., 0.], embedding_model='test-meaning-only@v1',
                                 lexical=lexical_document(['Học máy\nMachine learning']), meanings=meanings,
                                 created_at=now(), updated_at=now())
        db.content_bags.insert_one(bag.bson())
        assert ContentBagDocument.model_validate(db.content_bags.find_one({'_id': 'u'})).meanings == meanings
        assert check_meanings_schema(db, dimension=3, audit_documents=True)['ready']
        assert len(db.list_collection_names()) == 5
        bad = deepcopy(bag.bson())
        bad['meanings']['members'][0]['embedding'].pop()
        with pytest.raises(WriteError):
            db.content_bags.replace_one({'_id': 'u'}, bad)
