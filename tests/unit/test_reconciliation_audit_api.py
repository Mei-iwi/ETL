from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def test_audit_jobs_and_evidence_require_admin(container):
    with TestClient(create_app(container)) as client:
        for path in ('coverage', 'audit-jobs', 'file-evidence/unknown', 'runs/latest', 'discrepancies'):
            assert client.get('/api/v1/reconciliation/' + path).status_code == 403
        assert client.post('/api/v1/reconciliation/audit-jobs', json={}).status_code == 403
        container.settings.admin_enabled = True
        result = client.post('/api/v1/reconciliation/audit-jobs', json={'audit_type': 'LIGHT'})
        assert result.status_code == 202
        data = client.get('/api/v1/reconciliation/audit-jobs').json()
        assert data['items'][0]['job_id'] == result.json()['job_id']
        assert 'claim_token' not in str(data) and 'storage_object_key' not in str(data)
        assert client.get('/api/v1/reconciliation/coverage').json()['scheduler_enabled'] is False
        assert client.get('/api/v1/reconciliation/file-evidence/unknown').json()['status'] == 'NOT_VERIFIED'
        assert client.post('/api/v1/reconciliation/audit-jobs', json={'audit_type': 'IDS', 'stream': 'files'}).status_code == 422


def test_latest_does_not_mark_active_durable_lease_as_interrupted(container):
    from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
    audit = ScheduledAudit(container.reconciliation_engine, container.settings)
    key = audit.queue.enqueue()
    claim = audit.queue.claim()
    audit._save(claim, {'records_scanned': 0})
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        report = client.get('/api/v1/reconciliation/runs/latest?stream=files').json()['stream_reports'][0]
        assert report['run_id'] == key
        assert report['status'] == 'RUNNING'
        assert report['summary_meta'].get('reason') != 'RECONCILIATION_INTERRUPTED'
