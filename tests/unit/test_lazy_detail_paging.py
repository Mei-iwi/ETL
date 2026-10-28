"""Read-only paging contracts against isolated SQLite and deterministic artifacts."""
from copy import deepcopy

import pytest

from conftest import ingest
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient
from tests.unit.test_bilingual_media import finish
from tests.unit.test_indexing import ready


@pytest.mark.parametrize('count', [0, 1, 25])
def test_bilingual_summary_pages_revision_and_full_exports(container, count):
    version = ingest(container)
    finish(container, version)
    container.settings.admin_enabled = True
    with container.uow() as repo:
        job = repo.find('ocr_jobs', {'resource_version_id': version['id']}, order=('-created_at', '-id'), limit=1)[0]
        tasks = repo.find('ocr_page_tasks', {'job_id': job['id']}, order=('page_num', 'id'))
        result_id = None
        for task in tasks:
            result = repo.find('ocr_page_results', {'page_task_id': task['id']}, limit=1)[0]
            segments = [] if result_id is not None else [dict(segment_id=f's{i}', source_type='speech',
                original_text=f'Original {i}', translated_text=f'Translated {i}' if i % 2 == 0 else None,
                source_language='vi', target_language='en', translation_status='completed',
                start_time=i, end_time=i + 1, generated=False) for i in range(count)]
            repo.update('ocr_page_results', result['id'], {'result_metadata': {'paired_segments': segments}})
            result_id = result_id or result['id']
        before_jobs = repo.count('ocr_jobs', {})
    base = f'/api/v1/resource-versions/{version["id"]}'
    with TestClient(create_app(container)) as api:
        legacy = api.get(base + '/bilingual')
        assert legacy.status_code == 200 and len(legacy.json()['items']) == count
        assert 'summary' not in legacy.json() and 'pagination' not in legacy.json()
        metadata = api.get(base + '/bilingual?include_items=false')
        assert metadata.status_code == 200 and metadata.json()['items'] == []
        assert metadata.headers['cache-control'] == 'private, no-store'
        summary = metadata.json()['summary']
        assert summary['total'] == count and summary['translated_count'] == (count + 1) // 2
        assert summary['timed_count'] == count
        revision = summary['revision']
        pages = []
        for offset in range(0, max(count, 1), 20):
            page = api.get(base + f'/bilingual?limit=20&offset={offset}&revision={revision}')
            assert page.status_code == 200 and page.json()['summary'] == summary
            pages.extend(page.json()['items'])
        assert pages == legacy.json()['items']
        assert api.get(base + f'/bilingual?limit=20&offset=100&revision={revision}').json()['items'] == []
        for query in ('limit=0', 'limit=101', 'offset=-1', 'revision=bad', 'include_items=bad'):
            assert api.get(base + '/bilingual?' + query).status_code == 422
        assert api.get(base + '/exports/json').json()['items'] == pages
        if count:
            assert f'Original {count - 1}' in api.get(base + '/exports/txt').text
        with container.uow() as repo:
            repo.update('resource_versions', version['id'], {'processing_options': {'target_language': 'fr'}})
        assert api.get(base + f'/bilingual?limit=20&revision={revision}').status_code == 409
        assert api.get(base + '/bilingual?include_items=false').json()['summary']['revision'] != revision
        container.settings.admin_enabled = False
        assert api.get(base + '/bilingual?include_items=false').status_code == 403
    with container.uow() as repo:
        assert repo.count('ocr_jobs', {}) == before_jobs


def test_bag_wire_paging_stable_order_revision_and_legacy_contract(container, monkeypatch):
    index, version, _ = ready(container)
    index.enqueue(version['id'])
    assert index.once()
    original = index.status(version['id'])
    row = original['bags'][0]
    full = {**original, 'bag_count': 25, 'bags': [{**row, 'id': f'bag-{i:03d}'} for i in reversed(range(25))]}
    monkeypatch.setattr(index, 'status', lambda _: deepcopy(full))
    base = f'/api/v1/resource-versions/{version["id"]}/index-status'
    with TestClient(create_app(container)) as api:
        assert api.get(base).json()['bags'] == full['bags']
        assert 'bag_revision' not in api.get(base).json()
        assert api.get(base + '?include_bags=false').json()['bag_count'] == 25
        run = full['published_run_id']
        first = api.get(base + f'?bag_limit=10&expected_run_id={run}')
        assert first.status_code == 200
        revision = first.json()['bag_revision']
        pages = first.json()['bags']
        for offset in (10, 20):
            response = api.get(base + f'?bag_limit=10&bag_offset={offset}&expected_run_id={run}&bag_revision={revision}')
            assert response.status_code == 200
            assert response.json()['bag_pagination'] == {'offset': offset, 'limit': 10, 'total': 25}
            pages.extend(response.json()['bags'])
        assert [row['id'] for row in pages] == [f'bag-{i:03d}' for i in range(25)]
        assert api.get(base + '?bag_limit=10&bag_offset=30').json()['bags'] == []
        assert api.get(base + '?bag_limit=10&expected_run_id=other').status_code == 409
        for query in ('bag_limit=0', 'bag_limit=101', 'bag_offset=-1', 'bag_revision=bad'):
            assert api.get(base + '?' + query).status_code == 422
        full['bags'][0]['title'] = 'changed snapshot'
        assert api.get(base + f'?bag_limit=10&bag_revision={revision}').status_code == 409
        full['index_status'] = 'STALE'
        assert api.get(base + '?bag_limit=10').status_code == 409
        container.settings.admin_enabled = False
        assert api.get(base + '?bag_limit=10').status_code == 403


def test_bag_page_keeps_real_artifact_verification(container):
    index, version, _ = ready(container)
    index.enqueue(version['id'])
    assert index.once()
    # A live integrity failure must block paging even if the caller supplies a run.
    index.bags.rows.clear()
    with TestClient(create_app(container)) as api:
        base = f'/api/v1/resource-versions/{version["id"]}/index-status'
        assert api.get(base).json()['index_status'] != 'READY'
        assert api.get(base + '?bag_limit=10').status_code == 409
