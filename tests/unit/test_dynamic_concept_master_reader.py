"""Real isolated SQLAlchemy storage, SELECT-only enumeration; no Dev/Prod IO."""
import pytest
from conftest import completed
from sqlalchemy import event

from data_sync_etl.adapters.concept_corpus import CorpusReadOnlyAccessGate
from data_sync_etl.adapters.persistence.concept_corpus import MasterCorpusInventory
from data_sync_etl.domain.corpus_concepts import EligibilityFence
from data_sync_etl.ports.concept_corpus import ConceptBlocked


@pytest.mark.parametrize('readonly', ['on', 'off'])
def test_read_only_transaction_verified_before_select(readonly):
    from types import SimpleNamespace

    seen = []

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def execution_options(self, **options):
            seen.append(options)
            return self

        def begin(self):
            return self

        def scalar(self, statement):
            seen.append(str(statement))
            return readonly

        def execute(self, statement):
            seen.append(str(statement))

    inventory = MasterCorpusInventory(SimpleNamespace(dialect=SimpleNamespace(name='postgresql'),
        connect=lambda: Connection()), CorpusReadOnlyAccessGate(lambda: True))
    if readonly == 'off':
        with pytest.raises(ConceptBlocked, match='READ_ONLY_MASTER_REQUIRED'), inventory._read():
            pytest.fail('A writable transaction escaped the gate')
        assert len(seen) == 2
    else:
        with inventory._read():
            pass
        assert 'statement_timeout' in seen[2]
    assert seen[0] == {'isolation_level': 'REPEATABLE READ', 'postgresql_readonly': True}
    assert seen[1] == 'SHOW transaction_read_only'


def test_master_keyset_latest_version_review_and_deleted_are_authoritative(container):
    version, _ = completed(container)
    with container.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': version['id']})
        for unit in units:
            repo.update('content_units', unit['id'], {'review_status': 'APPROVED', 'retrieval_eligible': True})
    inventory = MasterCorpusInventory(container.engine, CorpusReadOnlyAccessGate(lambda: True), allow_sqlite_fixture=True)
    statements = []
    projections = []
    def record(conn, cursor, statement, params, context, many):
        statements.append(statement.split()[0].upper())
        projections.append(statement.split('FROM')[0])
    event.listen(container.engine, 'before_cursor_execute', record)
    try:
        row = inventory.current(version['resource_id'])
        assert row.version_id == version['id'] and row.public and row.ready
        assert row.approved_unit_ids == frozenset(unit['id'] for unit in units)
        assert inventory.page('', 1)[0].resource_id
    finally:
        event.remove(container.engine, 'before_cursor_execute', record)
    assert set(statements) == {'SELECT'}
    for projection in projections:
        assert not any(name in projection for name in ('storage_object_key', 'storage_bucket', 'error_message',
            'uploaded_by', 'created_by', 'master_deleted_by', 'master_deletion_reason'))
    # Read-only inventory does not pretend its resource epoch covers unit/Mongo edits.
    with pytest.raises(ConceptBlocked, match='PRIVACY'):
        EligibilityFence(inventory).capture()
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'master_deleted_at': version['created_at']})
    row = inventory.current(version['resource_id'])
    assert row.deleted and not row.public
