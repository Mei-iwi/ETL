"""Run SOURCE writeback E2E against disposable PostgreSQL/S3 services.

The script is deliberately environment-driven and never falls back to a
configured Neon URL.  It writes temporary owner contracts outside the
repository and emits only non-sensitive IDs/counts.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import tempfile
from pathlib import Path

import boto3
from sqlalchemy import create_engine, text

from data_sync_etl.composition import Container
from data_sync_etl.config import Settings


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f'{name} is required')
    return value


def contracts(root: Path) -> tuple[Path, Path, Path]:
    state = [
        'id', 'mongo_id', 'provider_id', 'resource_type_id', 'created_by', 'title',
        'publication_status', 'is_active', 'deleted_at', 'deleted_by', 'created_at',
        'updated_at', 'provider_name', 'provider_type', 'provider_is_active',
        'provider_deleted_at', 'provider_deleted_by', 'provider_created_at',
        'provider_updated_at', 'resource_type_code', 'resource_type_name_vi',
        'resource_type_is_active', 'resource_type_deleted_at', 'resource_type_deleted_by',
        'resource_type_created_at', 'resource_type_updated_at',
    ]
    columns = {field: field for field in (
        'id', 'provider_id', 'resource_type_id', 'created_by', 'title',
        'publication_status', 'is_active', 'deleted_at', 'deleted_by',
        'created_at', 'updated_at',
    )}
    metadata = {
        'backend': 'postgresql', 'namespace': 'postgresql:learning',
        'entity_type': 'resources', 'schema': 'public', 'table': 'learning_resources',
        'id_column': 'id', 'actions': ['create', 'update', 'delete'],
        'allowed_roles': ['ADMIN'], 'allowed_actor_ids': [], 'state_columns': state,
        'columns': columns, 'idempotency_column': None,
    }
    version = json.loads((Path(__file__).parents[1] / 'deploy/config/source-version-write-contract.staging.proposal.json').read_text())
    storage = json.loads((Path(__file__).parents[1] / 'deploy/config/source-object-storage-write-contract.staging.proposal.json').read_text())
    metadata_path = root / 'source-write-contract.json'
    version_path = root / 'source-version-write-contract.json'
    storage_path = root / 'source-storage-write-contract.json'
    metadata_path.write_text(json.dumps(metadata), encoding='utf-8')
    version_path.write_text(json.dumps(version), encoding='utf-8')
    storage.update({'provider_id': 'iso-source-storage', 'bucket': 'source-uploads'})
    storage_path.write_text(json.dumps(storage), encoding='utf-8')
    return metadata_path, version_path, storage_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-url', required=True)
    parser.add_argument('--source-writer-url', required=True)
    parser.add_argument('--master-url', required=True)
    parser.add_argument('--s3-endpoint', required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='etl-iso-writeback-') as temp:
        metadata_contract, version_contract, storage_contract = contracts(Path(temp))
        settings = Settings(
            _env_file=None,
            database_url=args.master_url,
            source_database_url=args.source_url,
            source_adapter='postgresql',
            source_namespace='postgresql:learning',
            source_metadata_writeback_enabled=True,
            source_metadata_writer_mode='postgresql',
            source_write_database_url=args.source_writer_url,
            source_write_contract_file=metadata_contract,
            source_write_allowed_actions=('create', 'update', 'delete'),
            source_write_auto_approval_enabled=True,
            source_version_writeback_enabled=True,
            source_version_write_contract_file=version_contract,
            source_version_write_allowed_actions=('create',),
            source_storage_write_enabled=True,
            source_storage_write_contract_file=storage_contract,
            source_storage_write_endpoint_url=args.s3_endpoint,
            source_storage_write_region='us-east-1',
            source_storage_write_bucket='source-uploads',
            source_storage_write_provider='s3_compatible',
            source_storage_write_provider_id='iso-source-storage',
            source_storage_write_access_key_id='test',
            source_storage_write_secret_access_key='test',
            app_environment='development',
            admin_enabled=True,
        )
        container = Container(settings)
        try:
            sync = container.sync.run(full=True)
            with container.uow() as repo:
                actor = next(row for row in repo.find('master_users')
                             if row.get('source_record_key') == 'user-1')
            actor_id = f'master:{actor["id"]}'
            provider_id, resource_type_id = 'provider-1', 'type-1'

            metadata_key = 'iso-create-meta-01'
            first = container.source_resource_create.request(
                {'provider_id': provider_id, 'resource_type_id': resource_type_id,
                 'title': 'Isolated metadata create', 'publication_status': 'DRAFT'},
                idempotency_key=metadata_key, actor_id=actor_id, role='ADMIN')
            replay = container.source_resource_create.request(
                {'provider_id': provider_id, 'resource_type_id': resource_type_id,
                 'title': 'Isolated metadata create', 'publication_status': 'DRAFT'},
                idempotency_key=metadata_key, actor_id=actor_id, role='ADMIN')
            assert first['request_id'] == replay['request_id']
            assert container.source_writeback_worker.once('isolated-e2e')
            metadata_status = container.source_resource_create.status_for_request(first['request_id'])
            assert metadata_status['master_reconciliation_status'] == 'MASTER_RECONCILED'

            content = b'%PDF-isolated-source-writeback\n'
            staged = container.source_create_stager.stage(
                io.BytesIO(content), idempotency_key='iso-create-file-01',
                filename='isolated.pdf', content_type='application/pdf', file_size=len(content))
            file_job = container.source_resource_create.request(
                {'provider_id': provider_id, 'resource_type_id': resource_type_id,
                 'title': 'Isolated PDF create', 'publication_status': 'DRAFT'},
                idempotency_key='iso-create-file-01', actor_id=actor_id, role='ADMIN',
                source_file=staged)
            assert container.source_writeback_worker.once('isolated-e2e-file')
            file_status = container.source_resource_create.status_for_request(file_job['request_id'])
            assert file_status['source_version_id']
            object_client = boto3.client('s3', endpoint_url=args.s3_endpoint,
                                         aws_access_key_id='test', aws_secret_access_key='test',
                                         region_name='us-east-1')
            objects = object_client.list_objects_v2(Bucket='source-uploads').get('KeyCount', 0)
            assert objects == 1

            with container.uow() as repo:
                created = repo.get('master_learning_resources', metadata_status['master_resource_id'])
            update = container.source_writeback.request(
                created['id'], {'title': 'Isolated automatic update'},
                expected_source_state_hash=created['source_state_hash'],
                idempotency_key='iso-update-01', actor_id=actor_id, role='ADMIN',
                auto_approve=True)
            assert update['status'] == 'WRITEBACK_PENDING_APPROVAL'
            assert container.source_writeback_worker.once('isolated-e2e-update')
            with container.uow() as repo:
                updated = repo.get('master_learning_resources', created['id'])
            assert updated['title'] == 'Isolated automatic update'

            delete = container.source_resource_delete.request(
                created['id'], expected_source_state_hash=updated['source_state_hash'],
                idempotency_key='iso-delete-01', actor_id=actor_id, role='ADMIN', approve=False)
            assert delete['status'] == 'SOURCE_DELETE_PENDING'
            approved = container.source_resource_delete.request(
                created['id'], expected_source_state_hash=updated['source_state_hash'],
                idempotency_key='iso-delete-01', actor_id=actor_id, role='ADMIN', approve=True)
            assert approved['status'] == 'SOURCE_DELETE_PENDING'
            assert container.source_writeback_worker.once('isolated-e2e-delete')

            file_master_id = file_status['master_resource_id']
            with container.uow() as repo:
                source_edit_target = repo.get('master_learning_resources', file_master_id)
            writer_engine = create_engine(args.source_writer_url, hide_parameters=True)
            with writer_engine.begin() as connection:
                connection.execute(text(
                    "UPDATE learning_resources SET title='SOURCE direct edit', updated_at=now() WHERE id=:id"),
                    {'id': source_edit_target['source_record_key']})
                source_count = connection.execute(text(
                    "SELECT count(*) FROM learning_resources WHERE title IN "
                    "('Isolated metadata create','Isolated automatic update','Isolated PDF create','SOURCE direct edit')"
                )).scalar_one()
            writer_engine.dispose()
            direct = container.sync.run('resources', full=False)
            with container.uow() as repo:
                canonical = repo.get('master_learning_resources', file_master_id)
            assert direct['resources']['records_read'] >= 1
            print(json.dumps({
                'status': 'PASS', 'source_sync': sync['resources']['records_read'],
                'metadata_request_id': first['request_id'],
                'metadata_source_id': metadata_status['source_resource_id'],
                'metadata_master_id': metadata_status['master_resource_id'],
                'file_request_id': file_job['request_id'],
                'file_version_id': file_status['source_version_id'],
                's3_object_count': objects, 'source_row_count_probe': source_count,
                'direct_source_sync_records': direct['resources']['records_read'],
                'canonical_title_after_direct_sync': canonical['title'],
            }, sort_keys=True))
            return 0
        finally:
            container.close()


if __name__ == '__main__':
    raise SystemExit(main())
