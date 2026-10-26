import hashlib
from datetime import timedelta

from data_sync_etl.application.reconciliation_engine import (
    FLAG_NOT_VERIFIED,
    FLAG_SYNCED,
    metadata_differences,
    serialize_json_value,
)
from data_sync_etl.db.models import (
    ResourceVersion,
    SyncReconciliationDiscrepancy,
    SyncReconciliationRun,
)
from data_sync_etl.domain.core import new_id, now
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def test_audit_values_preserve_file_hashes_but_never_account_credentials():
    value = {'source_hash': 'file-digest', 'user': {'email': 'fixture@example.test',
        'hashed_password': 'private', 'password_hash': 'private'}, 'items': [{'access_token': 'private'}]}
    result = serialize_json_value(value)
    assert result == {'source_hash': 'file-digest', 'user': {'email': 'fixture@example.test'}, 'items': [{}]}


def test_source_metadata_changes_ignore_credentials_and_denormalized_fields():
    assert metadata_differences('resources', {'title': 'new', 'provider_id': 'p'},
        {'title': 'old', 'provider_id': 'p', 'provider_name': 'Master only'}) == ['title']
    assert metadata_differences('users', {'email': 'same', 'hashed_password': 'private1'},
        {'email': 'same', 'hashed_password': 'private2'}) == []


def test_file_verification_does_not_mutate_metadata_and_rejects_escape(container, tmp_path):
    root = tmp_path / 'local'
    (root / 'learning-resources').mkdir(parents=True)
    data = b'read only file'
    (root / 'learning-resources' / 'good.bin').write_bytes(data)
    with container.uow() as repo:
        resource_id = new_id()
        repo.insert('master_learning_resources', {'id': resource_id, 'source_origin': 'LOCAL',
            'resource_type_id': 'type-1', 'resource_type_code': 'DOC', 'resource_type_name_vi': 'Tài liệu',
            'resource_type_is_active': True, 'publication_status': 'PUBLISHED', 'is_active': True})
        versions = []
        for ordinal, (bucket, key) in enumerate([
            ('learning-resources', 'good.bin'), ('source-bucket', 'source.bin'),
            ('learning-resources', '../../outside.bin')], 1):
            row = ResourceVersion(id=new_id(), resource_id=resource_id, version_number=ordinal,
                lifecycle_status='APPROVED', storage_bucket=bucket, storage_object_key=key,
                source_hash=hashlib.sha256(data).hexdigest())
            repo.session.add(row)
            versions.append(row)
        repo.session.flush()
        results = container.reconciliation_engine.verify_file_blobs(repo.session, root)
        assert next(row for row in results if row['record_id'] == versions[0].id)['discrepancy_flag'] == FLAG_SYNCED
        assert versions[0].source_file_size is None and versions[0].storage_file_size is None
        assert next(row for row in results if row['record_id'] == versions[2].id)['message'] == 'Unsafe storage path'
        repo.update('master_learning_resources', resource_id, {'source_origin': 'POSTGRESQL'})
        source_results = container.reconciliation_engine.verify_file_blobs(repo.session, root)
        assert all(row['discrepancy_flag'] == FLAG_NOT_VERIFIED for row in source_results)


def test_latest_only_pagination_is_not_truncated_and_repairs_are_rejected(container):
    container.settings.admin_enabled = True
    with container.uow() as repo:
        for offset in (-1, 0):
            stamp = now() + timedelta(days=offset)
            run = SyncReconciliationRun(run_id=new_id(), stream_name='users', status='COMPLETED',
                started_at=stamp, source_count=0, target_count=125, matched_count=0, mismatched_count=125)
            repo.session.add(run)
            repo.session.flush()
            for index in range(125):
                repo.session.add(SyncReconciliationDiscrepancy(id=new_id(), run_id=run.run_id,
                    stream_name='users', record_id=str(index), discrepancy_flag='ORPHAN_MASTER',
                    detected_at=stamp, resolution_status='UNRESOLVED'))
        expected = run.run_id
    with TestClient(create_app(container)) as client:
        response = client.get('/api/v1/reconciliation/discrepancies?latest_only=true&page=13&page_size=10')
        assert response.status_code == 200
        result = response.json()
        assert result['total'] == 125 and len(result['items']) == 5
        assert {row['run_id'] for row in result['items']} == {expected}
        assert client.post('/api/v1/reconciliation/runs', json={'dry_run': False}).status_code == 409
        container.reconciliation_engine._run_lock.acquire()
        try:
            assert client.post('/api/v1/reconciliation/runs', json={'dry_run': True}).status_code == 409
        finally:
            container.reconciliation_engine._run_lock.release()
