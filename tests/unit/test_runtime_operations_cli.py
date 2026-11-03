import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from data_sync_etl.cli.main import dispatch, parser
from data_sync_etl.domain.core import DomainError


def test_audit_cli_uses_inventory_without_process_or_contract_changes():
    args = parser().parse_args(['audit-source-ocr', '--limit', '5', '--force', '--prioritize-new'])
    inventory = SimpleNamespace(once=Mock(return_value=['mv1']), summary={'classified': 5, 'ABSENT': 1})
    container = SimpleNamespace(ocr=SimpleNamespace(source_owned=True, inventory=inventory))
    assert dispatch(container, args) == {'classified': 5, 'ABSENT': 1}
    inventory.once.assert_called_once_with(limit=5, force=True, prioritize_new=True)
    with pytest.raises(DomainError, match='SOURCE_OCR_PIPELINE_REQUIRED'):
        dispatch(SimpleNamespace(ocr=SimpleNamespace()), args)


@pytest.mark.parametrize('limit', ['0', '51', '-1'])
def test_audit_cli_rejects_unbounded_requests(limit):
    with pytest.raises(SystemExit) as error:
        parser().parse_args(['audit-source-ocr', '--limit', limit])
    assert error.value.code == 2


def test_contract_validation_is_offline_and_does_not_activate(monkeypatch, capsys):
    cli = importlib.import_module('data_sync_etl.cli.main')
    example = Path('deploy/config/source-ocr-contract.example.json')
    before = example.read_bytes()
    monkeypatch.setattr(cli, 'Container', lambda: pytest.fail('offline validation accessed runtime'))
    monkeypatch.setattr(cli.sys, 'argv', ['etl', 'validate-source-ocr-contract', '--file', str(example),
                                         '--namespace', 'postgresql:OWNER_APPROVED_NAMESPACE'])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == {
        'status': 'STRUCTURE_VALID', 'activated': False, 'namespace_scope': True}
    assert example.read_bytes() == before


def test_invalid_contract_error_hides_contents_without_runtime(tmp_path, monkeypatch, capsys):
    cli = importlib.import_module('data_sync_etl.cli.main')
    contract = tmp_path / 'private.json'
    contract.write_text('{"secret": "must never be printed"}', encoding='utf-8')
    monkeypatch.setattr(cli, 'Container', lambda: pytest.fail('invalid policy accessed runtime'))
    monkeypatch.setattr(cli.sys, 'argv', ['etl', 'validate-source-ocr-contract', '--file', str(contract),
                                         '--namespace', 'source-a'])
    assert cli.main() == 1
    captured = capsys.readouterr()
    assert 'secret' not in captured.out + captured.err
    assert 'must never be printed' not in captured.out + captured.err


def test_users_refresh_cli_honors_source_and_runs_once(monkeypatch):
    args = parser().parse_args(['run-users-refresh-worker', '--source-id', 'source-a',
                                '--interval-seconds', '30', '--once'])
    worker = Mock()
    worker.run.return_value = {'status': 'COMPLETED'}
    factory = Mock(return_value=worker)
    monkeypatch.setattr('data_sync_etl.workers.users_refresh.UsersRefreshWorker', factory)
    container = SimpleNamespace(sync=object(), settings=object())
    assert dispatch(container, args) == {'status': 'COMPLETED'}
    factory.assert_called_once_with(container.sync, container.settings, source_id='source-a', interval_seconds=30)
    worker.run.assert_called_once_with(once=True)
