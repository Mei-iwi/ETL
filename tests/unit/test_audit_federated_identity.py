import pytest

from data_sync_etl.adapters.persistence.audit_metadata import audit_master_ids
from data_sync_etl.adapters.persistence.scheduled_audit import ScheduledAudit
from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.adapters.source.registry import ConfiguredSourceRegistry
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.db.models import ReconciliationAuditJob as Job
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash
from data_sync_etl.domain.source_registry import SourceRegistration


def test_metadata_audit_and_targeted_repair_use_source_mapping_not_raw_master_id(container):
    class Source(JsonFixtureSourceAdapter):
        source_backend = 'postgresql'
        source_origin = 'POSTGRESQL'

    source = Source(container.settings.source_fixture_root)
    other = Source(container.settings.source_fixture_root)
    source.source_namespace, other.source_namespace = 'audit-school-a', 'audit-school-b'
    registry = ConfiguredSourceRegistry([
        (SourceRegistration(source_id=key, backend='postgresql', namespace=adapter.source_namespace,
                            adapter_type='isolated-fixture'), adapter)
        for key, adapter in [('SRC_A', source), ('SRC_B', other)]])
    sync = MasterSync(source, container.uow, source_registry=registry, federation_enabled=True)
    sync.run(full=True, source_id='SRC_A')
    with container.uow() as repo:
        mapped = repo.federated_master_id('SRC_A', source.locator('resources', 'resource-1'))
        assert mapped != 'resource-1'
        before = repo.source_checkpoint('SRC_A', 'resources')
    container.reconciliation_engine.source_adapter = source
    audit = ScheduledAudit(container.reconciliation_engine, container.settings, sync)
    for stream in ('resources', 'subjects'):
        job_id = audit.queue.enqueue('METADATA', stream, mode='FULL')
        audit.once(job_id)
        with container.uow() as repo:
            job = repo.session.get(Job, job_id)
            assert job.status == 'COMPLETED'
            assert job.checkpoint['matched_count'] == job.checkpoint['records_scanned'] > 0
            assert job.checkpoint['mismatched_count'] == 0
    result = sync.run_selected('resources', ['resource-1'])
    assert result['records_read'] == 1
    with container.uow() as repo:
        assert repo.source_checkpoint('SRC_A', 'resources')['last_id'] == before['last_id']
        assert repo.federated_master_id('SRC_A', source.locator('resources', 'resource-1')) == mapped


def test_audit_does_not_accept_same_raw_id_from_another_namespace(container):
    container.sync.run(full=True)
    foreign = SourceLocator(backend='postgresql', namespace='another-school',
                            entity_type='resources', source_record_id='resource-1')
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {
            'source_backend': foreign.backend, 'source_namespace': foreign.namespace,
            'source_record_key': foreign.source_record_id, 'source_entity_type': foreign.entity_type,
            'source_identity_version': foreign.version, 'source_identity_hash': source_identity_hash(foreign)})
    with container.uow() as repo:
        with pytest.raises(DomainError, match='BLOCKED_SOURCE_MAPPING'):
            audit_master_ids(repo, container.source, 'resources', ['resource-1'])
