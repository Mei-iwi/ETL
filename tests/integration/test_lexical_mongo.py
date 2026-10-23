"""Real Mongo contract/backfill checks, exclusively in fixture-owned UUID DBs."""

import json
from argparse import Namespace
from copy import deepcopy
from pathlib import Path

import pytest
from bson import json_util
from pymongo import MongoClient
from pymongo.errors import WriteError

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore, MongoContentReader
from data_sync_etl.adapters.mongo.lexical_schema import check_lexical_schema, lexical_contract
from data_sync_etl.adapters.mongo.schema import contracts, initialize
from data_sync_etl.domain.core import now
from data_sync_etl.domain.lexical import lexical_document
from data_sync_etl.domain.mongo_documents import ContentBagDocument
from scripts.reindex_lexical import run
from tests.unit import test_search

searchable = test_search.searchable


@pytest.mark.parametrize('searchable', [{'meanings': False}, {'meanings': True}], indirect=True)
def test_underthesea_upgrade_from_v1_preserves_dense_and_meanings(searchable, mongo_test_database, tmp_path):
    from data_sync_etl.adapters.mongo.lexical_schema import LEGACY_ANALYZER
    from data_sync_etl.adapters.mongo.meaning_schema import check_meanings_schema, meaning_contract

    c, version = searchable
    with MongoClient(mongo_test_database[0], tz_aware=True, serverSelectionTimeoutMS=3000) as client:
        db = client[mongo_test_database[1]]
        initialize(db, apply=True, dimension=3)
        meanings = c.settings.index_meaning_bags_enabled
        factory = meaning_contract if meanings else lexical_contract
        old_validator = factory(3, analyzer=LEGACY_ANALYZER)
        db.command('collMod', 'content_bags', validator=old_validator)
        for raw in c.documents.units.values():
            db.mongo_content_units.insert_one(deepcopy(raw))
        reader = MongoContentReader(db)
        for bag in c.indexing.bags.rows.values():
            raw = bag.bson()
            raw['lexical']['analyzer'] = LEGACY_ANALYZER
            raw['content_fingerprint'] = reader.content_fingerprint(
                raw['resource_version_id'], generation=raw['projection_generation'])
            db.content_bags.insert_one(raw)
        original = list(db.content_bags.find().sort('_id', 1))
        c.indexing.bags = c.content_bags = MongoBagStore(db, 3)
        c.indexing.reader = reader
        c.indexing.embedding.embed = lambda *_: pytest.fail('Lexical upgrade encoded content')
        assert c.indexing.status(version['id'])['index_status'] != 'READY'
        args = Namespace(apply=False, rollback=False, backup=tmp_path / 'v2-backup.json', writers_stopped=False)
        assert run(c, args)['mode'] == 'dry-run'
        assert list(db.content_bags.find().sort('_id', 1)) == original
        assert not args.backup.exists()
        args.apply = args.writers_stopped = True
        assert run(c, args)['status'] == 'COMPLETE'
        check = check_meanings_schema if meanings else check_lexical_schema
        assert check(db, dimension=3, audit_documents=True)['ready']
        assert c.indexing.status(version['id'])['index_status'] == 'READY'
        updated = list(db.content_bags.find().sort('_id', 1))
        assert [r['embedding'] for r in updated] == [r['embedding'] for r in original]
        assert [r.get('meanings') for r in updated] == [r.get('meanings') for r in original]
        args.apply, args.rollback = False, True
        assert run(c, args)['status'] == 'ROLLED_BACK'
        assert list(db.content_bags.find().sort('_id', 1)) == original
        assert next(db.list_collections(filter={'name': 'content_bags'}))['options']['validator'] == old_validator


@pytest.mark.parametrize('legacy_concepts', [False, True])
def test_backfill_dry_run_apply_resume_and_rollback(searchable, mongo_test_database, tmp_path, legacy_concepts):
    c, version = searchable
    client = MongoClient(mongo_test_database[0], tz_aware=True, serverSelectionTimeoutMS=3000)
    db = client[mongo_test_database[1]]
    try:
        initialize(db, apply=True, dimension=3)
        original_validator = contracts(3)['content_bags']
        if legacy_concepts:
            prop = json.loads((Path(__file__).parents[2] / 'fixtures/search/legacy_concept_property.json').read_text())
            original_validator['$jsonSchema']['properties']['concepts'] = prop
            db.command('collMod', 'content_bags', validator=original_validator)
        for raw in c.documents.units.values():
            db.mongo_content_units.insert_one(deepcopy(raw))
        for bag in c.indexing.bags.rows.values():
            raw = bag.bson()
            raw.pop('lexical')
            if legacy_concepts:
                raw['concepts'] = {'dictionary_version': 'old', 'fingerprint': 'a' * 64,
                                   'resolver_version': 'old', 'counts': {'old': 1}, 'evidence': []}
            raw['content_fingerprint'] = MongoContentReader(db).content_fingerprint(
                raw['resource_version_id'], generation=raw['projection_generation'])
            db.content_bags.insert_one(raw)
        original = list(db.content_bags.find().sort('_id', 1))
        store = MongoBagStore(db, 3)
        c.indexing.bags = c.content_bags = store
        c.indexing.reader = MongoContentReader(db)
        c.indexing.embedding.embed = lambda *_: pytest.fail('Backfill called model')
        args = Namespace(apply=False, rollback=False, backup=tmp_path / 'backup.json', writers_stopped=False)
        assert run(c, args)['mode'] == 'dry-run'
        assert list(db.content_bags.find().sort('_id', 1)) == original
        assert not args.backup.exists()
        args.apply = args.writers_stopped = True
        assert run(c, args)['status'] == 'COMPLETE'
        assert check_lexical_schema(db, dimension=3, audit_documents=True)['ready']
        assert c.indexing.status(version['id'])['index_status'] == 'READY'
        updated = list(db.content_bags.find().sort('_id', 1))
        assert all(row['lexical']['length'] > 0 for row in updated)
        assert all('concepts' not in row for row in updated)
        assert [row['embedding'] for row in updated] == [row['embedding'] for row in original]
        # Resume after interruption between durable PG run insertion and journal completion.
        journal = json_util.loads(args.backup.read_text(encoding='utf-8'))
        entry = journal['versions'][version['id']]
        with c.uow() as repo:
            repo.update('etl_stage_runs', entry['run_id'], {'status': 'RUNNING'})
        if legacy_concepts:
            # Interrupted apply can leave old concept rows after validator change.
            db.command('collMod', 'content_bags', validator=original_validator)
            db.content_bags.replace_one({'_id': original[0]['_id']}, original[0])
            db.command('collMod', 'content_bags', validator=lexical_contract(3))
        assert run(c, args)['status'] == 'COMPLETE'
        assert list(db.content_bags.find().sort('_id', 1)) == updated
        bad = deepcopy(updated[0])
        bad['lexical']['frequencies'][0] += 1
        with pytest.raises(WriteError):
            db.content_bags.replace_one({'_id': bad['_id']}, bad)
        args.apply, args.rollback = False, True
        assert run(c, args)['status'] == 'ROLLED_BACK'
        assert list(db.content_bags.find().sort('_id', 1)) == original
        installed = next(db.list_collections(filter={'name': 'content_bags'}))['options']['validator']
        assert installed == original_validator
    finally:
        client.close()


def test_extension_preserves_foundation_and_enforces_analyzer(mongo_test_database):
    client = MongoClient(mongo_test_database[0], tz_aware=True, serverSelectionTimeoutMS=3000)
    db = client[mongo_test_database[1]]
    try:
        initialize(db, apply=True, dimension=3)
        assert not check_lexical_schema(db, dimension=3)['ready']
        db.command('collMod', 'content_bags', validator=lexical_contract(3))
        # Builder-style serialization uses exclude_unset recursively: version
        # and analyzer must survive as explicit required BSON fields.
        bag = ContentBagDocument(_id='new-bag', resource_version_id='v', unit_type='TXT_TEXT',
            keywords=['learning'], embedding_text='learning', embedding=[.1, .2, .3],
            embedding_model='fixture-only@1', lexical=lexical_document(['learning rareword']),
            created_at=now(), updated_at=now())
        db.content_bags.insert_one(bag.bson())
        assert check_lexical_schema(db, dimension=3, audit_documents=True)['ready']
        assert len(list(db.list_collection_names())) == 5
    finally:
        client.close()
