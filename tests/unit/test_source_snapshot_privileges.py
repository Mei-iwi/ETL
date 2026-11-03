from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.source.postgresql import TABLES, PostgreSQLSourceAdapter
from data_sync_etl.domain.core import DomainError


@pytest.mark.parametrize('writable', [False, True])
def test_snapshot_checks_all_table_privileges_in_one_live_query(writable):
    engine = MagicMock()
    engine.dialect.name = 'postgresql'
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one.return_value = {'privileged': False, 'writable': writable}
    adapter = PostgreSQLSourceAdapter(engine=engine)
    if writable:
        with pytest.raises(DomainError, match='source account has write privileges'):
            with adapter.snapshot():
                pytest.fail('writable SOURCE reader must be rejected')
    else:
        with adapter.snapshot() as reader:
            assert reader.connection is connection
    connection.execute.assert_called_once()
    statement, parameters = connection.execute.call_args.args
    assert 'bool_or(has_table_privilege' in str(statement)
    assert set(parameters['tables']) == set(TABLES.values())
    assert 'rolbypassrls' in str(statement)
    assert "current_setting('lock_timeout')" in str(statement)


def test_snapshot_refuses_privileged_role_before_any_metadata_read():
    engine = MagicMock()
    engine.dialect.name = 'postgresql'
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.one.return_value = {'privileged': True, 'writable': False}
    with pytest.raises(DomainError, match='source account must be read-only'):
        with PostgreSQLSourceAdapter(engine=engine).snapshot():
            pytest.fail('privileged SOURCE role accepted')
    connection.execute.assert_called_once()
