import pytest
from sqlalchemy import inspect

from data_sync_etl.db.models import ReconciliationAuditJob
from data_sync_etl.domain.core import new_id
from tests.conftest import migrate


def test_additive_audit_upgrade_and_rollback_never_erases_history(container):
    migrate(container.engine, '0018', downgrade=True)
    before = set(inspect(container.engine).get_table_names())
    migrate(container.engine)
    assert set(inspect(container.engine).get_table_names()) == before | {
        'reconciliation_audit_jobs', 'reconciliation_file_states'}
    key = new_id()
    with container.uow() as repo:
        repo.session.add(ReconciliationAuditJob(id=key, job_key=key, audit_type='DEEP',
            stream_name='files', status='PENDING', checkpoint={}))
    with pytest.raises(RuntimeError, match='AUDIT_HISTORY_PRESERVED'):
        migrate(container.engine, '0018', downgrade=True)
    with container.uow() as repo:
        assert repo.session.get(ReconciliationAuditJob, key) is not None


def test_scheduler_off_does_not_require_new_tables_for_existing_sync(container):
    from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
    migrate(container.engine, '0018', downgrade=True)
    assert not container.settings.reconciliation_scheduler_enabled
    assert ScheduledAudit(container.reconciliation_engine, container.settings).schedule() == []
    # The running application requires the current MASTER schema for sync;
    # scheduler-disabled mode must not create audit tables on its own.
    migrate(container.engine)
    assert container.sync.run(full=True)['resources']['inserted'] == 3
    assert container.sync.run(full=False)['resources']['records_read'] == 0
