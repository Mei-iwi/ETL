"""Policy-gated automatic SOURCE intents from normal application actions."""

from data_sync_etl.domain.core import now
from tests.unit.test_source_metadata_writeback import prepare_writeback


def test_metadata_auto_policy_enqueues_and_worker_applies(container):
    resource, writer = prepare_writeback(container)
    container.settings.source_write_auto_approval_enabled = True
    response = container.source_writeback.request(
        'resource-1', {'title': 'Automatically queued'},
        expected_source_state_hash=resource['source_state_hash'],
        idempotency_key='auto-policy-01', actor_id='local-admin', role='ADMIN',
        auto_approve=True,
    )
    assert response['status'] == 'WRITEBACK_PENDING_APPROVAL'
    with container.uow() as repo:
        job = repo.find('reconciliation_audit_jobs', {'audit_type': 'SOURCE_WRITEBACK'})[0]
        assert job['checkpoint']['approved_by_policy'] is True
        assert job['checkpoint']['policy'] == 'SOURCE_PREAUTHORIZED_CONTRACT'
    assert container.source_writeback.process_pending_once('auto-policy-worker') is True
    assert writer.records['resource-1']['title'] == 'Automatically queued'


def test_restart_recovers_committed_create_reconciliation_without_rewriting_source(container, monkeypatch):
    prepare_writeback(container)
    with container.uow() as repo:
        repo.insert('reconciliation_audit_jobs', {
            'id': 'committed-create', 'job_key': 'SOURCE_CREATE_RESOURCE:committed-create',
            'audit_type': 'SOURCE_CREATE', 'stream_name': 'resources',
            'status': 'SOURCE_CREATE_APPLIED', 'created_at': now(),
            'checkpoint': {'source_resource_id': 'resource-1', 'reconciliation_required': True},
        })
    calls = []
    container.source_writeback_worker.reconcile_source = lambda: calls.append('readback')
    monkeypatch.setattr(container.source_resource_create, 'process_pending_once', lambda *_: False)
    assert container.source_writeback_worker.once('recovery')
    with container.uow() as repo:
        cp = repo.get('reconciliation_audit_jobs', 'committed-create')['checkpoint']
        assert cp['master_reconciliation_status'] == 'MASTER_RECONCILED'
        assert cp['master_resource_id'] == 'resource-1'
    assert calls == ['readback']
    assert not container.source_writeback_worker.once('recovery')
    assert calls == ['readback']

