"""P08 whole-root budget includes history; never deletes audit/source data."""
import pytest

from data_sync_etl.adapters.concept_artifacts import ArtifactStore
from data_sync_etl.adapters.concept_capacity import RootCapacity
from data_sync_etl.ports.concept_corpus import ConceptBlocked
from tests.unit.test_concept_followup_storage import build


def test_root_quota_counts_audit_staging_unknown_and_preserves_current(tmp_path):
    store = ArtifactStore(tmp_path / 'derived', create=True, max_bytes=1024 * 1024)
    first = build(store, activate=True)
    for name in ('review/audit.txt', 'staging/failed/sort.tmp', 'contributions/fixture.json', 'operator-note.txt'):
        target = store.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'x' * 1000)
    usage = store.capacity()
    assert {'review', 'staging', 'contributions', 'operator-note.txt'} <= set(usage['categories'])
    store.max_root_bytes = usage['logical_file_bytes'] + 1024
    with pytest.raises(ConceptBlocked, match='ROOT_QUOTA'):
        build(store, parent=first['generation'], token='another')
    assert store.load().manifest['generation'] == first['generation']
    assert store.path('review/audit.txt').read_bytes() == b'x' * 1000


def test_capacity_scan_is_bounded_and_does_not_change_files(tmp_path):
    target = tmp_path / 'data'
    target.write_text('preserve', encoding='utf-8')
    with pytest.raises(ConceptBlocked, match='SCAN_BUDGET'):
        RootCapacity(tmp_path, 1000, max_entries=0).snapshot()
    assert target.read_text() == 'preserve'


def test_quota_blocks_json_before_replacing_existing_state(tmp_path):
    store = ArtifactStore(tmp_path / 'state', create=True)
    store.write_json('contributions/current.json', {'valid': True})
    store.max_root_bytes = 1024
    with pytest.raises(ConceptBlocked, match='ROOT_QUOTA'):
        store.write_json('contributions/current.json', {'new': 'x' * 2000})
    assert store.read_json('contributions/current.json') == {'valid': True}


def test_cli_operator_budget_override_is_explicit_and_bounded(tmp_path, capsys):
    import json

    from data_sync_etl.cli.concepts import main
    root = tmp_path / 'operator-fixture'
    assert main(['preflight', '--root', str(root), '--root-quota-mb', '64', '--release-budget-mb', '4']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['root_capacity']['limit_bytes'] == 64 * 1024 * 1024
    rejected = tmp_path / 'not-created'
    assert main(['preflight', '--root', str(rejected), '--root-quota-mb', '-1']) == 2
    assert not rejected.exists()
