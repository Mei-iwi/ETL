"""Read-only metadata guard: no Mongo server or document access is needed."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore
from data_sync_etl.adapters.mongo.schema import INDEXES, check_schema, contracts, initialize
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.indexing import IndexingError


def catalog_database(dimension=384):
    database = MagicMock()
    database.name = 'isolated-schema-double'
    database.with_options.return_value = database
    catalog = [{'name': name, 'type': 'collection', 'options': {
        'validator': validator, 'validationLevel': 'strict', 'validationAction': 'error',
    }} for name, validator in contracts(dimension).items()]
    database.list_collections.side_effect = lambda: iter(deepcopy(catalog))
    collections = {}
    for name, expected in INDEXES.items():
        collection = MagicMock()
        collection.list_indexes.return_value = [
            {'name': i.name, 'key': dict(i.keys), 'unique': i.unique} for i in expected]
        collection.count_documents.return_value = 0
        collections[name] = collection
    database.__getitem__.side_effect = collections.__getitem__
    return database, catalog, collections


def test_quick_check_uses_all_five_contracts_without_scanning_or_writing():
    database, catalog, collections = catalog_database()
    assert check_schema(database, dimension=384)['schema_ready']
    MongoBagStore(database, 384).ensure_ready()
    for collection in collections.values():
        collection.count_documents.assert_not_called()
        collection.aggregate.assert_not_called()
        collection.create_index.assert_not_called()
    database.command.assert_not_called()
    database.create_collection.assert_not_called()
    catalog[0]['options']['validationAction'] = 'warn'
    with pytest.raises(IndexingError, match='BLOCKED_INDEX_SCHEMA'):
        MongoBagStore(database, 384).ensure_ready()


def test_all_mismatches_reported_and_apply_fails_before_any_ddl():
    database, catalog, collections = catalog_database(256)
    catalog[0]['options']['validationLevel'] = 'moderate'
    collections['content_bags'].list_indexes.return_value = []
    report = check_schema(database, dimension=384)
    assert not report['schema_ready']
    assert {i['path'] for i in report['validator_mismatches']} == {
        'content_bags.embedding.minItems', 'content_bags.embedding.maxItems'}
    assert len(report['missing_indexes']) == 2
    assert report['validation_mismatches'][0]['path'] == 'users.validationLevel'
    with pytest.raises(DomainError, match='Incompatible Mongo validator'):
        initialize(database, dimension=384, apply=True)
    database.command.assert_not_called()
    database.create_collection.assert_not_called()
    for collection in collections.values():
        collection.create_index.assert_not_called()


@pytest.mark.parametrize('change', ['order', 'unique', 'partial', 'missing'])
def test_index_mismatch_is_fail_closed(change):
    database, _, collections = catalog_database()
    indexes = collections['content_bags'].list_indexes.return_value
    if change == 'order':
        indexes[0]['key'] = dict(reversed(list(indexes[0]['key'].items())))
    elif change == 'unique':
        indexes[0]['unique'] = True
    elif change == 'partial':
        indexes[0]['partialFilterExpression'] = {'is_current': True}
    else:
        indexes.pop(0)
    report = check_schema(database, dimension=384)
    problem = report['missing_indexes' if change == 'missing' else 'incompatible_indexes']
    assert problem[0]['name'] == 'bags_version_type'
    assert not report['schema_ready']


def test_empty_and_legacy_extra_are_read_only():
    database, catalog, _ = catalog_database()
    catalog.append({'name': 'indexed_chunks', 'type': 'collection'})
    report = check_schema(database, dimension=384)
    assert report['schema_ready'] and report['extra_collections'] == ['indexed_chunks']
    database.__getitem__.assert_any_call('content_bags')
    assert all(call.args[0] != 'indexed_chunks' for call in database.__getitem__.call_args_list)
    catalog.clear()
    report = check_schema(database, dimension=384)
    assert len(report['missing_collections']) == 5
    assert not report['schema_ready']
    database.command.assert_not_called()
    database.create_collection.assert_not_called()


@pytest.mark.parametrize('installed_dimension,exit_code', [(384, 0), (256, 1)])
def test_cli_check_emits_json_for_incompatible_schema(monkeypatch, capsys,
                                                     installed_dimension, exit_code):
    from data_sync_etl.adapters.mongo import schema

    database, _, collections = catalog_database(installed_dimension)
    client = MagicMock()
    client.__getitem__.return_value = database
    monkeypatch.setattr(schema, 'MongoClient', lambda *args, **kwargs: client)
    monkeypatch.setattr(schema, 'Settings', lambda: SimpleNamespace(
        mongo_uri='test-only-not-a-real-connection', mongo_database=database.name,
        index_embedding_dimension=384))
    assert schema.main(['--check']) == exit_code
    report = json.loads(capsys.readouterr().out)
    assert report['schema_ready'] == (exit_code == 0)
    assert report['effective_expected_dimension'] == 384
    assert 'test-only-not-a-real-connection' not in repr(report)
    database.command.assert_not_called()
    database.create_collection.assert_not_called()
    for collection in collections.values():
        collection.create_index.assert_not_called()
    client.close.assert_called_once()


def test_installer_requires_explicit_dimension():
    with pytest.raises(TypeError, match='dimension'):
        initialize(MagicMock())


def test_empty_bootstrap_uses_existing_installer_and_explicit_dimension(monkeypatch):
    from data_sync_etl.adapters.mongo import schema

    database, catalog, _ = catalog_database()
    catalog.clear()
    installer = MagicMock(return_value={'schema_ready': True})
    monkeypatch.setattr(schema, 'initialize', installer)
    assert schema.bootstrap_empty(database, dimension=384)['mode'] == 'bootstrap-empty'
    installer.assert_called_once_with(database, dimension=384, apply=True)


def test_bootstrap_ready_database_is_read_only():
    from data_sync_etl.adapters.mongo.schema import bootstrap_empty

    database, _, collections = catalog_database()
    assert bootstrap_empty(database, dimension=384)['schema_ready']
    database.command.assert_not_called()
    database.create_collection.assert_not_called()
    for collection in collections.values():
        collection.create_index.assert_not_called()


@pytest.mark.parametrize('problem', ['missing_collection', 'dimension', 'missing_index', 'invalid_data', 'validators'])
def test_bootstrap_never_repairs_existing_database(problem):
    from data_sync_etl.adapters.mongo.schema import bootstrap_empty

    database, catalog, collections = catalog_database()
    if problem == 'missing_collection':
        catalog.pop()
    elif problem == 'dimension':
        catalog[-1]['options']['validator'] = contracts(256)['content_bags']
    elif problem == 'missing_index':
        collections['content_bags'].list_indexes.return_value = []
    elif problem == 'validators':
        for row in catalog:
            row['options'] = {}
    else:
        collections['users'].count_documents.return_value = 1
    with pytest.raises(DomainError, match='No schema changed'):
        bootstrap_empty(database, dimension=384)
    database.command.assert_not_called()
    database.create_collection.assert_not_called()
    for collection in collections.values():
        collection.create_index.assert_not_called()
