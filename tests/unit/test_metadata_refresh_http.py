"""Authenticated edit → stale read → worker refresh → search, memory Mongo double."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

from data_sync_etl.adapters.mongo.foundation_store import ProfileConflict, user_profile_revision
from data_sync_etl.domain.core import now
from data_sync_etl.domain.mongo_documents import ResourceDescriptionDocument
from tests.unit.test_metadata_master_refresh import refresh
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_single_admin_auth import configured as configured
from tests.unit.test_single_admin_auth import credentials as credentials
from tests.unit.test_single_admin_auth import csrf, login, make_client


def test_authenticated_description_edit_refresh_and_revoke(metadata, configured):
    c, version, _, service = metadata
    service.settings = c.search.settings = c.settings
    holder = [ResourceDescriptionDocument(_id='desc', created_at=now(), updated_at=now(),
        description='Nội dung cũ', tags=['ban đầu'])]
    def update(key, changes, revision):
        assert key == 'desc'
        if user_profile_revision(holder[0]) != revision:
            raise ProfileConflict('fixture revision')
        holder[0] = holder[0].model_copy(update={**changes, 'updated_at': now()})
    c.metadata_documents = service.documents = SimpleNamespace(
        get_resource=lambda _: holder[0], update_resource_description=update)
    service.reconcile(apply=True)
    with make_client(configured) as api:
        assert login(api, configured[1]).status_code == 200
        headers = csrf(api)
        current = api.get('/api/v1/resources/resource-1/description').json()
        payload = {'revision': current['revision'], 'description': 'Sự truyền nhiệt trong chất rắn', 'tags': ['dẫn nhiệt']}
        changed = api.patch('/api/v1/resources/resource-1/description', headers=headers, json=payload)
        assert changed.status_code == 200, changed.text
        assert api.patch('/api/v1/resources/resource-1/description', headers=headers, json=payload).status_code == 409
        def query(text):
            response = api.post('/api/v1/search', headers=headers, json={'query': text, 'method': 'bow'})
            assert response.status_code == 200, response.text
            return response.json()
        assert query('truyền nhiệt')['total'] == 0
        started = perf_counter()
        assert refresh(c, service).tick()['status'] == 'READY'
        lag = perf_counter() - started
        assert query('nội dung cũ')['total'] == 0
        item = query('truyền nhiệt')['items'][0]
        assert 'DESCRIPTION' in item['matched_fields'] and item['resource_version_id'] == version['id']
        assert item['context_id'] is None
        timings = {}
        for concurrency in (1, 2, 4):
            def request(_):
                start = perf_counter()
                assert query('truyền nhiệt')['total'] == 1
                return perf_counter() - start
            start = perf_counter()
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                values = sorted(pool.map(request, range(8)))
            timings[str(concurrency)] = {'samples': 8, 'p50_seconds': values[3],
                'p95_seconds': values[-1], 'throughput_rps': 8 / (perf_counter() - start)}
        with c.uow() as repo:
            repo.update('master_learning_resources', 'resource-1', {'publication_status': 'UNPUBLISHED'})
            assert not repo.find('content_units', {'resource_version_id': version['id']})
        assert query('truyền nhiệt')['total'] == 0
        evidence = os.environ.get('ETL_METADATA_AUDIT_EVIDENCE')
        if evidence:
            Path(evidence, 'http-fixture.json').write_text(json.dumps({
                'scope': 'authenticated in-process ASGI/SQLite; memory Mongo; dense double; NOT STAGING',
                'refresh_seconds': lag, 'concurrency': timings}, indent=2), encoding='utf-8')
