"""Round-trip upload identities and retained extraction state on real repositories."""

import pytest

from data_sync_etl.application.source_file_resolver import SourceFileResolver
from data_sync_etl.application.source_version_sync import SourceVersionSynchronizer
from data_sync_etl.domain.core import SourceFileError, now
from data_sync_etl.domain.source_ids import SourceLocator, source_identity_hash


class UploadedSource:
    source_backend = 'postgresql'
    source_namespace = 'postgresql:learning'

    def __init__(self):
        self.row = {
            'source_version_id': 'RSV001', 'resource_id': 'SOURCE_RESOURCE',
            'source_version_number': 1, 'parent_version_id': None,
            'source_hash': 'a' * 64, 'source_lifecycle_status': 'PENDING_PRE_REVIEW',
            'storage_bucket': 'uploads', 'storage_object_key': 'opaque/no-extension',
            'updated_at': now(), 'resource_active': True,
        }

    def locator(self, entity_type, record_id):
        return SourceLocator(backend=self.source_backend, namespace=self.source_namespace,
                             entity_type=entity_type, source_record_id=record_id)

    def version_page(self, after=None, size=100):
        return {'items': [dict(self.row)] if after is None else [], 'next_cursor': None}

    def get_version(self, record_id):
        return dict(self.row) if record_id == self.row['source_version_id'] else None


def prepare(container, *, receipt=True):
    container.sync.run(full=True)
    source = UploadedSource()
    locator = source.locator('resources', 'SOURCE_RESOURCE')
    with container.uow() as repo:
        repo.update('master_learning_resources', 'resource-1', {
            'source_origin': 'POSTGRESQL', 'source_identity_version': 1,
            'source_backend': locator.backend, 'source_namespace': locator.namespace,
            'source_entity_type': locator.entity_type, 'source_record_key': locator.source_record_id,
            'source_identity_hash': source_identity_hash(locator),
        })
        if receipt:
            repo.insert('reconciliation_audit_jobs', {
                'id': 'upload-receipt', 'job_key': 'upload-receipt', 'audit_type': 'SOURCE_CREATE',
                'stream_name': 'resources', 'status': 'SOURCE_CREATE_APPLIED',
                'checkpoint': {
                    'source_resource_id': 'SOURCE_RESOURCE', 'source_version_id': 'RSV001',
                    'source_hash': 'a' * 64, 'workflow': 'SOURCE_VERSION_COMMITTED',
                    'source_file': {'filename': 'lesson.pdf'},
                },
            })
        repo.insert('source_file_imports', {
            'id': 'legacy-unbound', 'source_system': 'postgresql', 'source_version_id': 'RSV001',
            'resource_id': 'SOURCE_RESOURCE', 'source_version_number': 1,
            'source_lifecycle_status': 'UNKNOWN', 'source_hash': 'a' * 64,
            'source_updated_at': now(), 'storage_bucket': 'uploads',
            'storage_object_key': 'opaque/no-extension', 'status': 'METADATA_ONLY',
        })
    return source


def test_upload_binds_legacy_inventory_and_resolves_actual_master_version(container):
    source = prepare(container)
    sync = SourceVersionSynchronizer(container.uow)
    assert sync.sync('postgresql', source)['inserted'] == 1
    with container.uow() as repo:
        row = repo.get('source_file_imports', 'legacy-unbound')
        assert row['resource_id'] == 'resource-1'
        assert row['source_lifecycle_status'] == 'PENDING_PRE_REVIEW'
        version_id = row['target_version_id']
        assert version_id != 'RSV001'
        assert len(repo.find('source_file_imports', {'source_version_id': 'RSV001'})) == 1
    resolver = SourceFileResolver(container.uow, source, container.source_files, container.storage,
                                  container.source_file_import.formats, container.settings, None)
    reference = resolver.resolve(version_id)
    assert reference.filename == 'lesson.pdf'
    assert reference.opaque_locator == 'opaque/no-extension'
    assert reference.resource_id == 'resource-1'
    source.row['source_lifecycle_status'] = 'REJECTED'
    with pytest.raises(SourceFileError, match='BLOCKED_SOURCE_POLICY'):
        resolver.resolve(version_id)


def test_source_readback_preserves_completed_processing_and_index_generation(container):
    source = prepare(container)
    sync = SourceVersionSynchronizer(container.uow)
    sync.sync('postgresql', source)
    with container.uow() as repo:
        imported = repo.get('source_file_imports', 'legacy-unbound')
        version = repo.get('resource_versions', imported['target_version_id'])
        options = {**version['processing_options'], 'content_availability': 'CONTENT_READY',
                   'completed_index_generation': 'retained-generation'}
        repo.update('resource_versions', version['id'], {'lifecycle_status': 'INGESTED', 'processing_options': options})
        repo.insert('ocr_jobs', {
            'id': 'completed-upload-job', 'resource_version_id': version['id'],
            'status': 'COMPLETED', 'total_pages': 1, 'completed_pages': 1, 'failed_pages': 0,
        })
        repo.update('source_file_imports', imported['id'], {
            'status': 'FAILED', 'last_error_code': 'SOURCE_VERSION_MUTATED'})
    sync.sync('postgresql', source)
    sync.sync('postgresql', source)
    with container.uow() as repo:
        imported = repo.get('source_file_imports', 'legacy-unbound')
        current = repo.get('resource_versions', version['id'])
        assert imported['target_version_id'] == version['id']
        assert imported['status'] == 'IMPORTED'
        assert current['lifecycle_status'] == 'INGESTED'
        assert current['processing_options'] == options
        jobs = repo.find('ocr_jobs', {'resource_version_id': version['id']})
        assert len(jobs) == 1 and jobs[0]['status'] == 'COMPLETED'


def test_external_pending_source_without_exact_receipt_remains_blocked(container):
    source = prepare(container, receipt=False)
    SourceVersionSynchronizer(container.uow).sync('postgresql', source)
    with container.uow() as repo:
        row = repo.get('source_file_imports', 'legacy-unbound')
        assert row['source_lifecycle_status'] == 'PENDING_PRE_REVIEW'
        assert row['source_resource_active'] is True
        version_id = row['target_version_id']
    resolver = SourceFileResolver(container.uow, source, container.source_files, container.storage,
                                  container.source_file_import.formats, container.settings, None)
    with pytest.raises(SourceFileError, match='BLOCKED_SOURCE_POLICY'):
        resolver.resolve(version_id)


def test_upload_preferences_precede_auto_ocr_and_later_sync_preserves_local_edits(container):
    source = prepare(container)
    with container.uow() as repo:
        job = repo.get('reconciliation_audit_jobs', 'upload-receipt')
        checkpoint = dict(job['checkpoint'])
        checkpoint['source_file'] = {'filename': 'lesson.pdf', 'processing_preferences': {'source_language': 'en'}}
        repo.update('reconciliation_audit_jobs', job['id'], {'checkpoint': checkpoint})
    sync = SourceVersionSynchronizer(container.uow)
    sync.sync('postgresql', source)
    with container.uow() as repo:
        row = repo.get('source_file_imports', 'legacy-unbound')
        version = repo.get('resource_versions', row['target_version_id'])
        assert version['processing_options']['source_language'] == 'en'
        repo.update('resource_versions', version['id'], {
            'processing_options': {**version['processing_options'], 'source_language': 'fr'}})
    sync.sync('postgresql', source)
    with container.uow() as repo:
        assert repo.get('resource_versions', version['id'])['processing_options']['source_language'] == 'fr'


@pytest.mark.parametrize(('status', 'normalized'), [('FAILED', 'PRIVATE'), ('CANCELLED', 'DELETED')])
def test_failed_or_cancelled_upload_never_inherits_processing_permission(container, status, normalized):
    source = prepare(container)
    source.row['source_lifecycle_status'] = status
    SourceVersionSynchronizer(container.uow).sync('postgresql', source)
    with container.uow() as repo:
        row = repo.get('source_file_imports', 'legacy-unbound')
        assert row['source_lifecycle_status'] == normalized
        assert row['source_resource_active'] is False
