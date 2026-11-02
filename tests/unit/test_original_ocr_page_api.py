from datetime import timedelta

from conftest import ingest

from data_sync_etl.domain.core import now
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def test_original_ocr_page_is_authorized_bounded_and_preserves_empty_pages(container):
    version = ingest(container)
    with container.uow() as repo:
        job = repo.insert('ocr_jobs', {'resource_version_id': version['id'], 'status': 'RUNNING',
                                       'total_pages': 3, 'completed_pages': 2, 'failed_pages': 0})
        first = repo.insert('ocr_page_tasks', {'job_id': job['id'], 'page_num': 1,
                                               'status': 'SUCCEEDED', 'retry_count': 0})
        second = repo.insert('ocr_page_tasks', {'job_id': job['id'], 'page_num': 2,
                                                'status': 'SUCCEEDED', 'retry_count': 0})
        repo.insert('ocr_page_tasks', {'job_id': job['id'], 'page_num': 3, 'status': 'PENDING', 'retry_count': 0})
        repo.insert('ocr_page_results', {'page_task_id': first['id'], 'raw_text': 'Tiếng Việt <script>',
                                         'engine_name': 'test'})
        repo.insert('ocr_page_results', {'page_task_id': second['id'], 'raw_text': '', 'engine_name': 'test'})
    base = f"/api/v1/resource-versions/{version['id']}/ocr-pages"
    query = f"job_id={job['id']}"
    with TestClient(create_app(container)) as client:
        assert client.get(base + '/1?' + query).status_code == 403
        container.settings.admin_enabled = True
        status = client.get(f"/api/v1/resource-versions/{version['id']}/etl-status")
        assert status.status_code == 200
        for key in ('version', 'latest_version'):
            assert 'storage_bucket' not in status.json()[key]
            assert 'storage_object_key' not in status.json()[key]
        response = client.get(base + '/1?' + query + '&offset=0&limit=5')
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'private, no-store'
        assert response.json()['text'] == 'Tiếng'
        assert response.json()['total_characters'] == len('Tiếng Việt <script>')
        second_page = client.get(base + '/2?' + query).json()
        assert second_page['available'] is True and second_page['text'] == ''
        pending = client.get(base + '/3?' + query).json()
        assert pending['available'] is False and pending['text'] is None
        assert client.get(base + '/4?' + query).status_code == 404
        assert client.get(base + '/1?' + query + '&limit=20001').status_code == 422
        assert client.get(base + '/1?job_id=not-the-job').status_code == 404
        with container.uow() as repo:
            repo.update('ocr_jobs', job['id'], {'status': 'COMPLETED', 'completed_pages': 3})
            repo.insert('ocr_jobs', {'resource_version_id': version['id'], 'status': 'RUNNING',
                                     'total_pages': 3, 'completed_pages': 0, 'failed_pages': 0,
                                     'created_at': now() + timedelta(seconds=1)})
        assert client.get(base + '/1?' + query).status_code == 409
        with container.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
        assert client.get(base + '/1?' + query).status_code == 404
        with container.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'],
                        {'is_active': True, 'master_deleted_at': now()})
        assert client.get(base + '/1?' + query).status_code == 404
        with container.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'],
                        {'master_deleted_at': None, 'source_origin': 'POSTGRESQL'})
        container.source_resolver.derived_index_authorized = lambda *_args, **_kwargs: False
        denied = client.get(base + '/1?' + query)
        assert denied.status_code == 403
        assert 'Tiếng' not in denied.text
