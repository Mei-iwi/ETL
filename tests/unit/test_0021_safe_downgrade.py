import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.fixture
def migration(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'alembic/versions/0021_component_ocr_read_models.py'
    spec = importlib.util.spec_from_file_location('safe_downgrade_0021', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    connection = Mock()
    connection.dialect = SimpleNamespace(name='postgresql')
    connection.in_transaction.return_value = True
    connection.execute.return_value.scalar.return_value = False
    monkeypatch.setattr(module.op, 'get_bind', lambda: connection)
    drop = Mock()
    monkeypatch.setattr(module.op, 'drop_table', drop)
    monkeypatch.setattr(module, 'inspect', lambda _: SimpleNamespace(has_table=lambda *_args, **_kwargs: True))
    return module, connection, drop


def test_empty_tables_are_locked_then_dropped_in_reverse_order(migration):
    module, connection, drop = migration
    module.downgrade()
    assert [call.args[0] for call in drop.call_args_list] == [
        'master_component_content_units', 'master_component_ocr_pages', 'master_component_ocr_jobs']
    assert all('LOCK TABLE' in str(call.args[0]) for call in connection.execute.call_args_list[:3])


@pytest.mark.parametrize('populated_or_external_fk', [4, 6])
def test_refuses_any_rows_or_external_dependency(migration, populated_or_external_fk):
    module, connection, drop = migration
    results = [Mock(scalar=Mock(return_value=index == populated_or_external_fk)) for index in range(8)]
    connection.execute.side_effect = results
    with pytest.raises(RuntimeError, match='rollback refused'):
        module.downgrade()
    drop.assert_not_called()


def test_refuses_non_transactional_connection(migration):
    module, connection, drop = migration
    connection.in_transaction.return_value = False
    with pytest.raises(RuntimeError, match='transactional'):
        module.downgrade()
    drop.assert_not_called()


def test_upgrade_does_not_recreate_tables_by_default(migration, monkeypatch):
    module, _connection, _drop = migration
    monkeypatch.delenv('ETL_APPROVE_COMPONENT_OCR_0021', raising=False)
    with pytest.raises(RuntimeError, match='0021 is disabled'):
        module.upgrade()
