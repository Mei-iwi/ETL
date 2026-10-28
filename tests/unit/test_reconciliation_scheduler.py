from datetime import timedelta

import pytest
from sqlalchemy import event, select

from data_sync_etl.adapters.persistence.audit_queue import AuditQueue, utc
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.db.models import ReconciliationFileState as FileState
from data_sync_etl.domain.core import DomainError, now
from tests.unit.test_reconcile_master import CompleteSource
from tests.unit.test_reconciliation_light_deep import local_file as local_file
from tests.unit.test_reconciliation_scope import delta as delta


def test_scheduler_off_does_no_database_work(container):
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    statements = []
    def record(*args):
        statements.append(args[2])
    event.listen(container.engine, 'before_cursor_execute', record)
    try:
        assert audit.schedule() == []
        assert statements == []
    finally:
        event.remove(container.engine, 'before_cursor_execute', record)


def test_claim_crash_recovery_and_obsolete_worker_is_fenced(container):
    queue = AuditQueue(container.uow, container.settings)
    key = queue.enqueue()
    first = queue.claim()
    assert first['id'] == key and queue.claim() is None
    with container.uow() as repo:
        repo.session.get(Job, key).lease_expires_at = now() - timedelta(seconds=1)
    second = queue.claim()
    assert second['claim_token'] != first['claim_token']
    with pytest.raises(DomainError, match='AUDIT_LEASE_LOST'):
        queue.heartbeat(first)
    queue.fail(first)
    with container.uow() as repo:
        assert repo.session.get(Job, key).claim_token == second['claim_token']
    queue.heartbeat(second)


def test_schedule_keys_are_idempotent_and_sync_checkpoints_untouched(container):
    container.settings.reconciliation_scheduler_enabled = True
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    with container.uow() as repo:
        before = repo.find('sync_checkpoints')
    assert audit.schedule() == audit.schedule()
    with container.uow() as repo:
        assert repo.find('sync_checkpoints') == before
        assert len(list(repo.session.scalars(select(Job)))) == 10


def test_deep_runs_release_database_and_do_not_rehash_fresh_evidence(local_file, monkeypatch):
    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    first = audit.queue.enqueue()
    assert audit.once()
    with c.uow() as repo:
        row = repo.session.get(Job, first)
        assert row.status == 'COMPLETED', row.error_code
        assert row.checkpoint['bytes_read'] == 8
        state = repo.session.get(FileState, version_id)
        assert state.last_verified_hash is not None and state.verification_method == 'SHA256'
    second = audit.queue.enqueue()
    assert audit.once()
    with c.uow() as repo:
        row = repo.session.get(Job, second)
        assert row.status == 'COMPLETED'
        assert row.checkpoint['bytes_read'] == row.checkpoint['sha256_computed_count'] == 0
    assert audit.coverage()['recent_sha256_evidence_count'] == 1


def test_resume_after_budget_and_failure_backoff(local_file):
    c, path, version_id = local_file
    c.settings.reconciliation_max_files_per_run = 1
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    first = audit.queue.enqueue()
    audit.once()
    with c.uow() as repo:
        row = repo.session.get(Job, first)
        assert row.status == 'PARTIAL'
        assert row.checkpoint['last_id'] == version_id
    second = audit.queue.enqueue()
    audit.once()
    with c.uow() as repo:
        row = repo.session.get(Job, second)
        assert row.checkpoint['cycle_finished'] is True
        assert row.checkpoint['bytes_read'] == 0
    # A transient failure keeps an auditable retry and never writes business rows.
    missing = audit.queue.enqueue('IDS', 'resources')
    audit.engine.source_adapter = None
    audit.once()
    with c.uow() as repo:
        row = repo.session.get(Job, missing)
        assert row.status == 'RETRY_WAIT'
        assert row.next_retry_at is not None and row.error_code == 'AUDIT_UNAVAILABLE'


def test_authoritative_id_sweep_excludes_local_and_rechecks_deletion(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-3', {'source_origin': 'POSTGRESQL'})
    source = CompleteSource()
    container.reconciliation_engine.source_adapter = source
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    job_id = audit.queue.enqueue('IDS', 'resources')
    audit.once()
    from data_sync_etl.db.models import SyncReconciliationDiscrepancy
    with container.uow() as repo:
        row = repo.session.get(Job, job_id)
        assert row.status == 'COMPLETED', row.error_code
        rows = list(repo.session.scalars(select(SyncReconciliationDiscrepancy).where(SyncReconciliationDiscrepancy.run_id == job_id)))
        assert [(r.record_id, r.discrepancy_flag) for r in rows] == [('resource-3', 'ORPHAN_MASTER')]
        assert repo.get('master_learning_resources', 'resource-3')['is_active'] is True


def test_metadata_queue_uses_same_delta_scope_and_bounded_resume(delta):
    c, engine, source = delta
    from sqlalchemy import text
    with source.begin() as conn:
        conn.execute(text("UPDATE subjects SET subject_name='changed',updated_at=:stamp WHERE id IN ('s0000','s0001')"), {'stamp': now().isoformat()})
    audit = ScheduledAudit(engine, c.settings)
    c.settings.reconciliation_max_files_per_run = 100
    c.settings.reconciliation_audit_batch_size = 20
    # Initial audit intentionally scans a full metadata generation in bounded jobs.
    first = audit.queue.enqueue('METADATA', 'subjects')
    audit.once()
    with c.uow() as repo:
        row = repo.session.get(Job, first)
        assert row.status == 'PARTIAL', row.error_code
        assert row.checkpoint['records_scanned'] == 100
        after = row.checkpoint['last_id']
    second = audit.queue.enqueue('METADATA', 'subjects')
    audit.once()
    with c.uow() as repo:
        row = repo.session.get(Job, second)
        assert row.status == 'PARTIAL'
        assert row.checkpoint['last_id'] > after
        assert row.checkpoint['mismatched_count'] == 0


def test_missing_source_coverage_cannot_report_deletion(container):
    source = CompleteSource()
    source.partial = True
    container.reconciliation_engine.source_adapter = source
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    container.settings.reconciliation_audit_batch_size = 1
    key = audit.queue.enqueue('IDS', 'resources')
    audit.once()
    with container.uow() as repo:
        row = repo.session.get(Job, key)
        assert row.status == 'RETRY_WAIT'


def test_fencing_rejects_file_result_after_owner_replacement(local_file, monkeypatch):
    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue()
    engine_class = type(c.reconciliation_engine)
    original = engine_class.verify_file_snapshot
    replacement = []
    def expired(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        with c.uow() as repo:
            repo.session.get(Job, key).lease_expires_at = now() - timedelta(seconds=1)
        replacement.append(audit.queue.claim())
        return result
    monkeypatch.setattr(engine_class, 'verify_file_snapshot', expired)
    assert audit.once()
    from data_sync_etl.db.models import SyncReconciliationRun
    with c.uow() as repo:
        job = repo.session.get(Job, key)
        assert job.status == 'RUNNING'
        assert job.claim_token == replacement[0]['claim_token']
        assert repo.session.get(FileState, version_id) is None
        assert repo.session.get(SyncReconciliationRun, key).matched_count == 0


def test_scheduled_metadata_compares_resource_subject_and_grade_relations(container):
    from tests.unit.test_sync import change
    container.sync.run(full=True)
    change(container, 'resource_subjects', lambda rows: rows.pop(0))
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    key = audit.queue.enqueue('METADATA', 'resources')
    assert audit.once()
    from data_sync_etl.db.models import SyncReconciliationDiscrepancy
    with container.uow() as repo:
        job = repo.session.get(Job, key)
        assert job.status == 'COMPLETED', job.error_code
        rows = list(repo.session.scalars(select(SyncReconciliationDiscrepancy).where(SyncReconciliationDiscrepancy.run_id == key)))
        assert any(r.discrepancy_flag == 'DESYNC_METADATA' and '_resource_subjects' in r.source_value for r in rows)


def test_new_version_is_selected_without_rehashing_unchanged_version(local_file):
    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.queue.enqueue()
    audit.once()
    with c.uow() as repo:
        version = repo.get('resource_versions', version_id)
        values = {k: v for k, v in version.items() if k not in ('id', 'created_at', 'updated_at')}
        values['version_number'] = 2
        repo.insert('resource_versions', values)
    key = audit.queue.enqueue()
    audit.once()
    with c.uow() as repo:
        job = repo.session.get(Job, key)
        assert job.status == 'COMPLETED', job.error_code
        assert job.checkpoint['sha256_computed_count'] == 1
        assert job.checkpoint['bytes_read'] == 8
    assert audit.coverage()['recent_sha256_evidence_count'] == 2


def test_coverage_does_not_credit_evidence_for_replaced_locator(local_file):
    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.queue.enqueue()
    audit.once()
    with c.uow() as repo:
        repo.update('resource_versions', version_id, {'storage_object_key': 'different.pdf'})
    assert audit.coverage()['recent_sha256_evidence_count'] == 0
    c.settings.admin_enabled = True
    from data_sync_etl.main import create_app
    from tests.local_admin_client import TestClient
    with TestClient(create_app(c)) as client:
        evidence = client.get('/api/v1/reconciliation/file-evidence/' + version_id).json()
        assert evidence['status'] == 'NOT_VERIFIED'
        assert evidence['reason_code'] == 'AUDIT_EVIDENCE_STALE'


def test_light_size_match_cannot_clear_known_deep_corruption(local_file):
    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.queue.enqueue()
    audit.once()
    path.write_bytes(b'corrupt!')
    with c.uow() as repo:
        repo.session.get(FileState, version_id).next_due_at = now() - timedelta(seconds=1)
    audit.queue.enqueue()
    audit.once()
    audit.queue.enqueue('LIGHT', 'files')
    audit.once()
    with c.uow() as repo:
        assert repo.session.get(FileState, version_id).status == 'DESYNC_CHECKSUM'
    assert audit.coverage()['recent_sha256_evidence_count'] == 0


def test_failed_audit_does_not_touch_sync_checkpoints_or_launch_processing(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        before = repo.find('sync_checkpoints')
        jobs_before = repo.count('ocr_jobs', {})
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    audit.engine.source_adapter = None
    audit.queue.enqueue('METADATA', 'resources')
    audit.once()
    with container.uow() as repo:
        assert repo.find('sync_checkpoints') == before
        assert repo.count('ocr_jobs', {}) == jobs_before
        from data_sync_etl.db.models import SyncReconciliationRun
        run = repo.session.scalar(select(SyncReconciliationRun).where(SyncReconciliationRun.stream_name == 'resources'))
        assert run.status == 'PARTIAL'
        assert run.summary_meta['error_code'] == 'AUDIT_UNAVAILABLE'
    result = container.sync.run(full=False)
    assert result['resources']['records_read'] == 0  # No delta after the completed baseline.
    assert all(row['failed'] == 0 for row in result.values())


def test_light_storage_revision_change_prioritizes_deep_before_due_date(local_file):
    c, path, version_id = local_file
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    audit.queue.enqueue('LIGHT', 'files')
    audit.once()
    audit.queue.enqueue()
    audit.once()
    with c.uow() as repo:
        assert utc(repo.session.get(FileState, version_id).next_due_at) > now()
    path.write_bytes(b'corrupt!')  # Same size, new storage revision.
    audit.queue.enqueue('LIGHT', 'files')
    audit.once()
    with c.uow() as repo:
        assert utc(repo.session.get(FileState, version_id).next_due_at) <= now()
    key = audit.queue.enqueue()
    audit.once()
    with c.uow() as repo:
        assert repo.session.get(FileState, version_id).status == 'DESYNC_CHECKSUM'
        assert repo.session.get(Job, key).checkpoint['sha256_computed_count'] == 1


def test_scheduler_off_pauses_old_scheduled_jobs_but_allows_manual_jobs(container):
    container.settings.reconciliation_scheduler_enabled = True
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    audit.schedule()
    container.settings.reconciliation_scheduler_enabled = False
    assert audit.queue.claim() is None
    manual = audit.queue.enqueue()
    assert audit.queue.claim()['id'] == manual
