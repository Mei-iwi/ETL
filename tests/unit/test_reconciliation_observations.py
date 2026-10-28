from data_sync_etl.domain.reconciliation import observe_file_result
from tests.unit.test_reconciliation_light_deep import local_file as local_file


def test_observed_bytes_are_not_hash_evidence_or_io_bytes():
    metrics = {'bytes_read': 0, 'matched_count': 0, 'sha256_computed_count': 0}
    observe_file_result(metrics, {'discrepancy_flag': 'NOT_VERIFIED', 'target_value': {
        'verification': 'SIZE_ONLY', 'reason_code': 'SHA256_NOT_CHECKED',
        'storage_file_size': 5525995, 'size_status': 'SIZE_MATCHED'}})
    assert metrics == {'bytes_read': 0, 'matched_count': 0, 'sha256_computed_count': 0,
        'observed_file_bytes': 5525995, 'observed_file_count': 1, 'sha256_not_checked_count': 1}


def test_unknown_sizes_do_not_turn_into_empty_file_measurements():
    metrics = {}
    observe_file_result(metrics, {'discrepancy_flag': 'NOT_VERIFIED', 'target_value': {'storage_file_size': None}})
    assert metrics == {}
    observe_file_result(metrics, {'discrepancy_flag': 'NOT_VERIFIED', 'target_value': {'storage_file_size': 0}})
    assert metrics == {'observed_file_bytes': 0, 'observed_file_count': 1}


def test_size_mismatches_are_not_counted_as_pending_only_hashes():
    metrics = {}
    observe_file_result(metrics, {'discrepancy_flag': 'DESYNC_BLOB_SIZE', 'target_value': {
        'verification': 'SIZE_ONLY', 'reason_code': 'SHA256_NOT_CHECKED',
        'storage_file_size': 42, 'size_status': 'SIZE_MISMATCH'}})
    assert metrics == {'observed_file_bytes': 42, 'observed_file_count': 1}


def test_quick_file_job_publishes_measured_bytes_but_never_claims_sha256(local_file):
    from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
    from data_sync_etl.db.models import ReconciliationAuditJob

    container, path, _ = local_file
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    job_id = audit.queue.enqueue('LIGHT', 'files')
    audit.once()
    with container.uow() as repo:
        job = repo.session.get(ReconciliationAuditJob, job_id)
        assert job.status == 'PARTIAL'
        assert job.checkpoint['observed_file_bytes'] == path.stat().st_size
        assert job.checkpoint['observed_file_count'] == 1
        assert job.checkpoint['sha256_not_checked_count'] == 1
        assert job.checkpoint['bytes_read'] == job.checkpoint['sha256_computed_count'] == 0
        assert job.checkpoint['matched_count'] == 0
