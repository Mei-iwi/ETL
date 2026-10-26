"""Migration0016 evidence for approved audit tables/file sizes; test DB only."""
from sqlalchemy import inspect

from tests.conftest import migrate


def test_audit_additions_match_history_and_business_tables_survive(container):
    engine = container.engine
    audit = {'sync_reconciliation_runs', 'sync_reconciliation_discrepancies'}
    migrate(engine, '0015', downgrade=True)
    tables_before = set(inspect(engine).get_table_names()) - {'alembic_version'}
    version_before = {row['name'] for row in inspect(engine).get_columns('resource_versions')}
    import_before = {row['name'] for row in inspect(engine).get_columns('source_file_imports')}
    assert len(tables_before) == 18 and not tables_before & audit
    migrate(engine, '0016')
    assert set(inspect(engine).get_table_names()) - {'alembic_version'} == tables_before | audit
    for table, before in (('resource_versions', version_before), ('source_file_imports', import_before)):
        assert {row['name'] for row in inspect(engine).get_columns(table)} == before | {'source_file_size', 'storage_file_size'}
    fk = inspect(engine).get_foreign_keys('sync_reconciliation_discrepancies')
    assert any(row['referred_table'] == 'sync_reconciliation_runs' for row in fk)
