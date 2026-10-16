from conftest import completed
from fastapi.testclient import TestClient
from sqlalchemy import event

from data_sync_etl.domain.core import now
from data_sync_etl.main import create_app


def listing(container):
    with TestClient(create_app(container)) as client:
        response = client.get('/api/v1/resources?page=1&size=7&q=')
        assert response.status_code == 200, response.text
        return response.json()


def test_resources_without_versions_preserve_pagination(container):
    container.sync.run(full=True)
    result = listing(container)
    assert result['pagination']['total'] == 3
    assert all(item['latest_version'] is None for item in result['items'])


def test_latest_version_not_previous_completed_version_and_state_refresh(container):
    first, _ = completed(container)
    old = next(item for item in listing(container)['items'] if item['id'] == first['resource_id'])
    assert old['latest_version']['processing_status'] == 'CONTENT_READY'
    with container.uow() as repo:
        second = repo.insert('resource_versions', {
            'resource_id': first['resource_id'], 'version_number': 2, 'parent_version_id': first['id'],
            'lifecycle_status': 'INGESTED', 'storage_bucket': first['storage_bucket'],
            'storage_object_key': first['storage_object_key'], 'source_hash': 'b' * 64,
        })
    current = next(item for item in listing(container)['items'] if item['id'] == first['resource_id'])
    assert current['latest_version']['id'] == second['id']
    assert current['latest_version']['version_number'] == 2
    assert current['latest_version']['processing_status'] == 'NOT_STARTED'
    assert current['updated_at'] == old['updated_at']  # Version creation need not change resource metadata.
    assert not {'source_hash', 'storage_bucket', 'storage_object_key', 'job', 'stages'} & current['latest_version'].keys()
    job = container.ocr.create_job(second['id'])
    current = next(item for item in listing(container)['items'] if item['id'] == first['resource_id'])
    assert current['latest_version']['processing_status'] == 'OCR_PENDING'
    with container.uow() as repo:
        repo.update('ocr_jobs', job['id'], {'status': 'FAILED'})
    current = next(item for item in listing(container)['items'] if item['id'] == first['resource_id'])
    assert current['latest_version']['processing_status'] == 'OCR_FAILED'


def test_source_reference_and_receipt_time_use_etl_version_creation(container):
    version, job = completed(container)
    with container.uow() as repo:
        repo.insert('source_file_imports', {
            'source_system': 'postgresql', 'source_version_id': 'source-v2',
            'resource_id': version['resource_id'], 'source_version_number': 2,
            'source_lifecycle_status': 'APPROVED', 'source_updated_at': now(),
            'storage_bucket': 'source', 'storage_object_key': 'sample.pdf', 'source_hash': version['source_hash'],
            'target_version_id': version['id'], 'processing_job_id': job['id'], 'status': 'PROCESSING',
        })
    item = next(item for item in listing(container)['items'] if item['id'] == version['resource_id'])
    assert item['latest_version']['source_version_id'] == 'source-v2'
    assert item['latest_version']['processing_status'] == 'CONTENT_READY'  # Do not use import PROCESSING as ETL state.
    assert item['latest_version']['created_at'].startswith(version['created_at'].date().isoformat())


def test_list_fetches_version_summaries_in_constant_query_count(container):
    completed(container)
    statements = []
    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(container.engine, 'before_cursor_execute', record)
    try:
        container.resource_listing.run(size=7)
    finally:
        event.remove(container.engine, 'before_cursor_execute', record)
    assert len(statements) == 6  # Count + page + latest versions/jobs/stages/source refs.
