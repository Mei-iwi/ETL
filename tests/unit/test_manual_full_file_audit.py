from datetime import timedelta

from data_sync_etl.adapters.persistence.audit_schedule import AuditSchedule
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.db.models import ReconciliationAuditJob as Job, ResourceVersion, SyncReconciliationRun
from data_sync_etl.domain.core import new_id, now
from tests.unit.test_reconciliation_light_deep import local_file as local_file


def due(c, key):
    with c.uow() as repo:
        repo.session.get(Job, key).finished_at = now() - timedelta(seconds=c.settings.reconciliation_retry_seconds + 1)


def test_manual_full_visits_all_versions_across_batches_with_scheduler_off(local_file):
    c, path, version = local_file
    c.settings.reconciliation_max_files_per_run = 1
    with c.uow() as repo:
        existing = repo.get('resource_versions', version)
        values = {key: value for key, value in existing.items() if key not in ('id', 'created_at', 'updated_at')}
        repo.session.add(ResourceVersion(id=new_id(), **{**values, 'version_number': 2}))
        imports, processing = repo.count('source_file_imports', {}), repo.count('ocr_jobs', {})
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    assert c.settings.reconciliation_scheduler_enabled is False
    assert audit.once(key)
    with c.uow() as repo:
        row = repo.session.get(Job, key)
        assert row.status == 'PARTIAL'
        assert row.checkpoint['manual_full_scan']
        assert row.checkpoint['records_scanned'] == 1
    assert audit.queue.resume_full_files() == []  # Respect the interval between batches.
    due(c, key)
    assert AuditSchedule(c.uow, c.settings).tick() == [key]
    assert audit.queue.resume_full_files() == []  # One pending continuation, same durable job.
    assert audit.once(key)
    with c.uow() as repo:
        row = repo.session.get(Job, key)
        assert row.status == 'PARTIAL'
        assert row.checkpoint['cycle_records_scanned'] == 1
        assert row.checkpoint['records_scanned'] == 1
    due(c, key)
    assert audit.queue.resume_full_files() == [key]
    assert audit.once(key)
    with c.uow() as repo:
        job, run = repo.session.get(Job, key), repo.session.get(SyncReconciliationRun, key)
        assert job.status == 'COMPLETED'
        assert job.checkpoint['cycle_finished'] is True
        assert run.source_count == run.target_count == run.matched_count == 2
        assert job.checkpoint['cycle_bytes_read'] == 16
        assert not run.summary_meta['whole_repository_verified']
        assert repo.count('source_file_imports', {}) == imports
        assert repo.count('ocr_jobs', {}) == processing
    assert audit.queue.resume_full_files() == []
    assert path.read_bytes() == b'original'


def test_full_scan_preserves_unverified_result_and_stops_after_visiting_scope(local_file):
    c, _, _ = local_file
    c.settings.reconciliation_max_files_per_run = 1
    c.settings.reconciliation_max_bytes_per_run = 7
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    key = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    audit.once(key)
    due(c, key)
    assert audit.queue.resume_full_files() == [key]
    audit.once(key)
    with c.uow() as repo:
        job, run = repo.session.get(Job, key), repo.session.get(SyncReconciliationRun, key)
        assert job.status == 'PARTIAL' and job.checkpoint['cycle_finished']
        assert job.checkpoint['cycle_not_verified_count'] == 1
        assert run.source_count == run.target_count == 1 and run.matched_count == 0
    due(c, key)
    assert audit.queue.resume_full_files() == []


def test_new_explicit_full_scan_starts_fresh_and_does_not_bypass_failed_retry(local_file):
    c, _, _ = local_file
    c.settings.reconciliation_max_files_per_run = 1
    audit = ScheduledAudit(c.reconciliation_engine, c.settings)
    first = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    audit.once(first)
    second = audit.queue.enqueue('DEEP', 'files', mode='FULL')
    with c.uow() as repo:
        assert not repo.session.get(Job, second).checkpoint.get('last_id')
        assert not repo.session.get(Job, second).checkpoint.get('cycle_records_scanned')
        repo.session.get(Job, first).status = 'FAILED'
    due(c, first)
    assert audit.queue.resume_full_files() == []
