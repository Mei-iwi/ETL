from copy import deepcopy

from data_sync_etl.domain.core import new_id
from tests.unit.test_evaluation import api
from tests.unit.test_evaluation import evaluation as evaluation
from tests.unit.test_search import searchable as searchable


def seed(c, command):
    with api(c) as client:
        dataset = client.post('/api/v1/evaluations/datasets', json=command).json()
        result = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset['dataset_id']})
        assert result.status_code == 201
    return c.evaluations.store.get('runs', result.json()['run_id'])


def test_history_sorted_paged_and_read_only(evaluation, monkeypatch):
    c, _, command = evaluation
    template = seed(c, command)
    ids = []
    for index in range(9):
        run = deepcopy(template)
        run.update(id=template['id'] if index == 0 else new_id(),
                   created_at=f'2026-10-{index + 1:02d}T10:00:00+00:00',
                   duration_ms=1234, status='FAILED' if index == 8 else 'COMPLETED')
        c.evaluations.store.put('runs', run, replace=index == 0)
        ids.append(run['id'])
    calls = []
    corpus = c.evaluations.corpus
    monkeypatch.setattr(c.evaluations, 'corpus', lambda *a: (calls.append(1), corpus(*a))[1])
    monkeypatch.setattr(c.search, 'search', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('No recompute')))
    with api(c) as client:
        first = client.get('/api/v1/evaluations/runs')
        assert first.status_code == 200, first.text
        assert first.headers['Cache-Control'] == 'private, no-store'
        body = first.json()
        assert body['total'] == 9 and body['total_pages'] == 2 and body['size'] == 7
        assert [row['id'] for row in body['items']] == list(reversed(ids))[:7]
        assert len(calls) == 1  # Read-permission snapshot reused within this request only.
        row = body['items'][0]
        assert row['duration_ms'] == 1234 and row['status'] == 'FAILED' and row['query_count'] == 2
        assert not {'queries', 'observations', 'snapshot', 'summary'} & row.keys()
        second = client.get('/api/v1/evaluations/runs?page=2').json()
        assert [row['id'] for row in second['items']] == [ids[1], ids[0]]
        assert client.get('/api/v1/evaluations/runs?page=99').json()['items'] == []
        for query in ('page=0', 'size=0', 'size=51', 'page=bad'):
            assert client.get('/api/v1/evaluations/runs?' + query).status_code == 422
        c.settings.search_internal_dev_enabled = False
        assert client.get('/api/v1/evaluations/runs').status_code == 403


def test_history_revoked_resources_hidden_and_corrupt_files_fail_closed(evaluation):
    c, version, command = evaluation
    run = seed(c, command)
    with c.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
    with api(c) as client:
        body = client.get('/api/v1/evaluations/runs')
        assert body.status_code == 200 and body.json()['items'] == [] and body.json()['total'] == 0
        path = c.evaluations.store.directory('runs') / (run['id'] + '.json')
        path.write_text('{}', encoding='utf-8')  # Disposable fixture only.
        assert client.get('/api/v1/evaluations/runs').status_code == 503


def test_history_empty_and_remote_denied(evaluation):
    from fastapi.testclient import TestClient

    from data_sync_etl.main import create_app

    c, _, _ = evaluation
    with api(c) as client:
        body = client.get('/api/v1/evaluations/runs')
        assert body.status_code == 200 and body.json()['total_pages'] == 1
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('198.51.100.1', 50000)) as client:
        assert client.get('/api/v1/evaluations/runs').status_code == 403
