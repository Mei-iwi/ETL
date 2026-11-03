from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.source.postgresql import TABLES, PostgreSQLSourceAdapter
from data_sync_etl.domain.core import DomainError


@pytest.mark.parametrize('writable', [False, True])
def test_snapshot_checks_all_table_privileges_in_one_live_query(writable):
    engine = MagicMock()
    engine.dialect.name = 'postgresql'
    connection = engine.connect.return_value.execution_options.return_value.__enter__.return_value
    connection.execute.return_value.scalar_one.return_value = False
    connection.scalar.return_value = writable
    adapter = PostgreSQLSourceAdapter(engine=engine)
    if writable:
        with pytest.raises(DomainError, match='source account has write privileges'):
            with adapter.snapshot():
                pytest.fail('writable SOURCE reader must be rejected')
    else:
        with adapter.snapshot() as reader:
            assert reader.connection is connection
    connection.scalar.assert_called_once()
    statement, parameters = connection.scalar.call_args.args
    assert 'bool_or(has_table_privilege' in str(statement)
    assert set(parameters['tables']) == set(TABLES.values())
