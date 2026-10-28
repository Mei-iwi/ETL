"""Audit comparisons against real read-only SOURCE and independently migrated MASTER."""
import pytest
from sqlalchemy import select, text

from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.db.models import SyncReconciliationDiscrepancy as Discrepancy
from tests.integration.source_postgres.test_delete_reconciliation import remove_fixture_row

pytestmark = pytest.mark.postgres


def setup_audit(source, target):
    MasterSync(source.adapter, target.uow, batch_size=2).run(full=True)
    target.reconciliation_engine.source_adapter = source.adapter
    return ScheduledAudit(target.reconciliation_engine, target.settings)


def test_real_source_metadata_relations_and_delta_scope(source, pg_container):
    # Unchanged seed rows must predate the audit's inclusive replay window.
    with source.engine.begin() as connection:
        connection.execute(text("SET LOCAL session_replication_role='replica'"))
        connection.execute(text("UPDATE learning_resources SET updated_at=now()-interval '1 day'"))
    audit = setup_audit(source, pg_container)
    initial = audit.queue.enqueue('METADATA', 'resources')
    audit.once()
    with pg_container.uow() as repo:
        job = repo.session.get(Job, initial)
        assert job.status == 'COMPLETED', job.error_code
        assert job.checkpoint['matched_count'] == 3
    with source.engine.begin() as connection:
        connection.execute(text("UPDATE learning_resources SET title='Audit changed one row' WHERE id='resource-1'"))
    delta = audit.queue.enqueue('METADATA', 'resources')
    audit.once()
    with pg_container.uow() as repo:
        job = repo.session.get(Job, delta)
        assert job.status == 'COMPLETED', job.error_code
        assert job.checkpoint['records_scanned'] == 1
        rows = list(repo.session.scalars(select(Discrepancy).where(Discrepancy.run_id == delta)))
        assert [(r.record_id, r.discrepancy_flag) for r in rows] == [('resource-1', 'DESYNC_METADATA')]


def test_real_authoritative_id_sweep_reports_without_tombstoning(source, pg_container):
    audit = setup_audit(source, pg_container)
    remove_fixture_row(source, 'learning_resources', 'resource-3')
    key = audit.queue.enqueue('IDS', 'resources')
    audit.once()
    with pg_container.uow() as repo:
        job = repo.session.get(Job, key)
        assert job.status == 'COMPLETED', job.error_code
        rows = list(repo.session.scalars(select(Discrepancy).where(Discrepancy.run_id == key)))
        assert [(r.record_id, r.discrepancy_flag) for r in rows] == [('resource-3', 'ORPHAN_MASTER')]
        assert repo.get('master_learning_resources', 'resource-3')['is_active'] is True
