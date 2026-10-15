"""Safety gates for the operator-only planner, not a new runtime migration system."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import mongo_dev_preflight as preflight
from tests.unit.test_schema_diagnostics import catalog_database


def test_plan_is_read_only_and_additive_for_current_dev_shape():
    database, catalog, collections = catalog_database()
    catalog.pop()  # Missing ContentBag, the other four have no validators/indexes.
    for row in catalog:
        row['options'] = {}
    for collection in collections.values():
        collection.list_indexes.return_value = []
        collection.aggregate.side_effect = lambda *args, **kwargs: iter([])
    report = preflight.plan(database, 384)
    assert report['status'] == 'READY_FOR_APPROVAL'
    assert len(report['operations']) == 11
    assert len([op for op in report['operations'] if op['operation'] == 'collMod']) == 4
    assert report['plan_sha256'] == preflight.plan(database, 384)['plan_sha256']
    database.command.assert_not_called()
    database.create_collection.assert_not_called()
    for collection in collections.values():
        collection.create_index.assert_not_called()


@pytest.mark.parametrize('problem', ['invalid', 'validator', 'index', 'options', 'duplicate'])
def test_plan_blocks_unsafe_changes_without_writes(problem):
    database, catalog, collections = catalog_database()
    if problem == 'invalid':
        collections['content_bags'].count_documents.return_value = 1
    elif problem == 'validator':
        catalog[-1]['options']['validator'] = preflight.contracts(256)['content_bags']
    elif problem == 'index':
        collections['content_bags'].list_indexes.return_value[0]['unique'] = True
    elif problem == 'options':
        catalog[0]['options']['validationAction'] = 'warn'
    else:
        collections['ocr_pages'].list_indexes.return_value = []
        collections['ocr_pages'].aggregate.return_value = iter([{'n': 2}])
    assert preflight.plan(database, 384)['status'] == 'BLOCKED'
    database.command.assert_not_called()
    database.create_collection.assert_not_called()


def test_no_changes_and_extra_legacy_preserved():
    database, catalog, collections = catalog_database()
    extra = MagicMock()
    extra.list_indexes.return_value = []
    collections['indexed_chunks'] = extra
    catalog.append({'name': 'indexed_chunks', 'type': 'collection', 'options': {}})
    report = preflight.plan(database, 384)
    assert report['status'] == 'NO_CHANGES'
    assert report['schema_check']['extra_collections'] == ['indexed_chunks']
    extra.create_index.assert_not_called()
    extra.drop.assert_not_called()


@pytest.mark.parametrize('name', ['etl_content', 'production', 'etl_restore_verify_fake'])
def test_rehearsal_cannot_write_dev_or_unowned_database(name):
    database = MagicMock()
    database.name = name
    with pytest.raises(ValueError, match='BLOCKED_NOT_ISOLATED'):
        preflight.rehearse(database, 384, {})
    database.list_collections.assert_not_called()
    database.command.assert_not_called()


def test_rehearsal_checks_snapshot_and_postcheck(monkeypatch):
    database = MagicMock()
    database.name = 'etl_restore_verify_' + 'a' * 32
    before = {'users': {'documents': 2, 'documents_sha256': 'unchanged'}}
    monkeypatch.setattr(preflight, 'snapshot', lambda _: before)
    monkeypatch.setattr(preflight, 'plan', lambda *args: {'blockers': [], 'status': 'NO_CHANGES'})
    installer = MagicMock(return_value={'schema_ready': True})
    monkeypatch.setattr(preflight, 'initialize', installer)
    assert preflight.rehearse(database, 384, before)['documents_unchanged']
    installer.assert_called_once_with(database, dimension=384, apply=True)
    with pytest.raises(ValueError, match='BLOCKED_RESTORE_VERIFICATION'):
        preflight.rehearse(database, 384, {})
    installer.return_value = {'schema_ready': False}
    with pytest.raises(ValueError, match='BLOCKED_REHEARSAL_POSTCHECK'):
        preflight.rehearse(database, 384, before)


@pytest.mark.parametrize('uri,database', [
    ('mongodb://127.0.0.1:27018', 'production'),
    ('mongodb://127.0.0.2:27018', 'etl_content'),
    ('mongodb://127.0.0.1:27017', 'etl_content'),
])
def test_reject_target_before_docker_or_schema_access(uri, database):
    settings = SimpleNamespace(mongo_uri=uri, mongo_database=database, mongo_metadata_mode='shared')
    client = MagicMock()
    with pytest.raises(ValueError, match='BLOCKED_UNEXPECTED_TARGET'):
        preflight.verify_target(settings, client, 'mongo')
    client.admin.command.assert_not_called()


def test_compound_index_order_affects_plan_fingerprint():
    assert preflight.fingerprint({'key': {'a': 1, 'b': 1}}) != preflight.fingerprint(
        {'key': {'b': 1, 'a': 1}})


def test_no_execute_switch_is_exposed():
    with pytest.raises(SystemExit):
        preflight.main(['--execute'])


def test_script_is_not_imported_by_runtime():
    root = Path(__file__).resolve().parents[2] / 'src' / 'data_sync_etl'
    assert all('mongo_dev_preflight' not in file.read_text(encoding='utf-8')
               for file in root.rglob('*.py'))
