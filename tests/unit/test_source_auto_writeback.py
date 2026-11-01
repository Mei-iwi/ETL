"""Policy-gated automatic SOURCE intents from normal application actions."""

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

