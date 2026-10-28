import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.adapters.storage.evaluation_store import EvaluationFileStore
from data_sync_etl.application.evaluation import EvaluationService
from data_sync_etl.domain.core import new_id
from data_sync_etl.domain.evaluation import EvaluationError
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


@pytest.fixture
def evaluation(searchable, tmp_path):
    c, version = searchable
    c.evaluations = EvaluationService(c.search, EvaluationFileStore(tmp_path / 'evaluation'), c.settings)
    command = {'name': '=SAMPLE isolated manually labelled', 'ground_truth_confirmed': True, 'relevance_unit': 'resource',
               'queries': [{'query_id': 'q1', 'query': 'page', 'relevance': [{'item_id': version['resource_id'], 'grade': 3}]},
                           {'query_id': 'q2', 'query': 'unrelatedkeyword', 'relevance': [{'item_id': version['resource_id'], 'grade': 0}]}]}
    return c, version, command


def api(c):
    return TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000))


def test_four_apis_persist_reload_and_report_without_recompute(evaluation):
    c, version, command = evaluation
    with c.uow() as repo:
        before = repo.find('etl_stage_runs', {})
    with api(c) as client:
        created = client.post('/api/v1/evaluations/datasets', json=command)
        assert created.status_code == 201, created.text
        dataset = created.json()
        assert dataset['relevance_unit'] == 'resource' and dataset['relevant_items'] == 1
        response = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset['dataset_id'], 'k_values': [1, 5]})
        assert response.status_code == 201, response.text
        assert response.json()['status'] == 'COMPLETED', response.text
        run_id = response.json()['run_id']
        c.evaluations.store = EvaluationFileStore(c.evaluations.store.root)
        c.search.search = lambda *args, **kwargs: pytest.fail('GET must never execute Search')
        view = client.get(f'/api/v1/evaluations/runs/{run_id}?offset=1&limit=2')
        assert view.status_code == 200, view.text
        body = view.json()
        assert body['pagination']['total'] == 6 and len(body['observations']) == 2
        assert len(body['summary']) == 3
        assert body['summary']['bow']['metrics']['5']['precision']['value'] == .1
        assert body['summary']['bow']['metrics']['5']['recall']['excluded_queries'] == 1
        summary = client.get(f'/api/v1/evaluations/runs/{run_id}?include_observations=false')
        assert summary.status_code == 200 and summary.headers['cache-control'] == 'private, no-store'
        assert summary.json()['observations'] == []
        assert summary.json()['pagination']['total'] == 6
        assert summary.json()['summary'] == body['summary']
        assert summary.json()['corpus_fingerprint'] == body['corpus_fingerprint']
        assert client.get(f'/api/v1/evaluations/runs/{run_id}?include_observations=invalid').status_code == 422
        csv = client.get(f'/api/v1/evaluations/runs/{run_id}/report')
        assert csv.status_code == 200 and csv.content.startswith(b'\xef\xbb\xbf')
        assert "'=SAMPLE" in csv.text and 'bag-cosine' not in csv.text
        report = client.get(f'/api/v1/evaluations/runs/{run_id}/report?format=json')
        assert report.status_code == 200 and report.json()['id'] == run_id
        assert 'embedding' not in body['observations'][0]
        assert 'storage_root' not in json.dumps(body)
        c.settings.search_internal_dev_enabled = False
        for method, path, payload in [
            ('post', '/api/v1/evaluations/datasets', command),
            ('post', '/api/v1/evaluations/runs', {'dataset_id': dataset['dataset_id']}),
            ('get', f'/api/v1/evaluations/runs/{run_id}', None),
            ('get', f'/api/v1/evaluations/runs/{run_id}?include_observations=false', None),
            ('get', f'/api/v1/evaluations/runs/{run_id}/report', None),
        ]:
            assert client.request(method, path, json=payload).status_code == 403
    with c.uow() as repo:
        assert before == repo.find('etl_stage_runs', {})


@pytest.mark.parametrize('change', ['duplicate_query', 'duplicate_label', 'unknown', 'bag_unit', 'invalid_grade', 'empty'])
def test_dataset_rejects_invalid_labels(evaluation, change):
    c, _, command = evaluation
    if change == 'duplicate_query':
        command['queries'].append(command['queries'][0])
    elif change == 'duplicate_label':
        command['queries'][0]['relevance'] *= 2
    elif change == 'unknown':
        command['queries'][0]['relevance'][0]['item_id'] = 'unknown'
    elif change == 'bag_unit':
        command['relevance_unit'] = 'content_bag'
    elif change == 'empty':
        command['queries'] = []
    else:
        command['queries'][0]['relevance'][0]['grade'] = 4
    with api(c) as client:
        response = client.post('/api/v1/evaluations/datasets', json=command)
        assert response.status_code == (403 if change == 'unknown' else 422)


def test_scope_changes_and_stale_corpus_are_not_comparable(evaluation):
    c, version, command = evaluation
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        original = c.search.search
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            with c.uow() as repo:
                repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
            return result
        c.search.search = changed
        run = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id}).json()
        assert run['status'] == 'NOT_COMPARABLE'
        assert client.get(f'/api/v1/evaluations/runs/{run["run_id"]}/report').status_code in (403, 409)
        assert client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id}).status_code in (409, 403)


def test_failed_runtime_is_not_a_successful_zero(evaluation):
    from data_sync_etl.domain.indexing import IndexingError

    c, _, command = evaluation
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        c.indexing.embedding.prepare = lambda: (_ for _ in ()).throw(IndexingError('BLOCKED_EMBEDDING_RUNTIME'))
        response = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id})
        assert response.json()['status'] == 'PARTIAL'
        run_id = response.json()['run_id']
        view = client.get(f'/api/v1/evaluations/runs/{run_id}').json()
        assert view['summary']['vector']['metrics']['1']['precision']['value'] is None
        assert client.get(f'/api/v1/evaluations/runs/{run_id}/report').status_code == 409


def test_file_store_quota_reload_tamper_path_and_lock(tmp_path):
    store = EvaluationFileStore(tmp_path, max_files=1)
    identifier = new_id()
    payload = {'id': identifier, 'queries': [], 'scope': 'local_demo'}
    store.put('datasets', payload)
    assert EvaluationFileStore(tmp_path).get('datasets', identifier) == payload
    with pytest.raises(EvaluationError):
        store.put('datasets', {'id': new_id()})
    with pytest.raises(EvaluationError):
        store.get('datasets', '../secret')
    with store.lock('run'), pytest.raises(EvaluationError):
        with store.lock('run'):
            pass
    path = tmp_path / 'evaluations' / 'datasets' / f'{identifier}.json'
    document = json.loads(path.read_text('utf-8'))
    document['payload']['scope'] = 'public'
    path.write_text(json.dumps(document), 'utf-8')
    with pytest.raises(EvaluationError):
        store.get('datasets', identifier)


def test_simultaneous_creates_never_overwrite(tmp_path):
    store = EvaluationFileStore(tmp_path)
    identifier = new_id()
    def write():
        try:
            store.put('datasets', {'id': identifier})
            return True
        except EvaluationError:
            return False
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(lambda _: write(), range(4))) == 1
    assert store.get('datasets', identifier) == {'id': identifier}


def test_remote_and_forwarded_requests_denied_for_all_endpoints(evaluation):
    c, _, command = evaluation
    paths = [('post', '/api/v1/evaluations/datasets', command),
             ('post', '/api/v1/evaluations/runs', {'dataset_id': new_id()}),
             ('get', f'/api/v1/evaluations/runs/{new_id()}', None),
             ('get', f'/api/v1/evaluations/runs/{new_id()}/report', None)]
    for peer, headers in [('198.51.100.1', {}), ('127.0.0.1', {'X-Forwarded-For': '127.0.0.1'})]:
        with TestClient(create_app(c), base_url='http://127.0.0.1', client=(peer, 50000)) as client:
            for method, path, body in paths:
                assert client.request(method, path, json=body, headers=headers).status_code == 403


def test_warmups_excluded_and_stale_dataset_not_run(evaluation, monkeypatch):
    from importlib import import_module

    c, version, command = evaluation
    clock, calls = [0.], []
    original = c.search.search
    def timed(*args, **kwargs):
        calls.append(args[1])
        clock[0] += .05 if len(calls) <= 3 else .01
        return original(*args, **kwargs)
    monkeypatch.setattr(import_module('data_sync_etl.application.evaluation'), 'perf_counter', lambda: clock[0])
    c.search.search = timed
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        response = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id, 'warmup_queries': 1})
        assert response.status_code == 201 and response.json()['status'] == 'COMPLETED'
        run = client.get(f'/api/v1/evaluations/runs/{response.json()["run_id"]}').json()
        assert len(calls) == 9 and len(run['observations']) == 6
        assert all(row['latency_ms'] == pytest.approx(10.) for row in run['observations'])
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'title': 'Changed after human labelling'})
        response = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'EVALUATION_DATASET_STALE'


def test_exact_four_routes_and_run_parameters(evaluation):
    c, _, _ = evaluation
    with api(c) as client:
        paths = client.get('/openapi.json').json()['paths']
        assert len([path for path in paths if path.startswith('/api/v1/evaluations/')]) == 4
        for payload in [{'methods': ['bow']}, {'k_values': [5,1]}, {'k_values': [1,1]},
                        {'max_results': 21}, {'k_values': [True]}, {'warmup_queries': 4}]:
            assert client.post('/api/v1/evaluations/runs', json={'dataset_id': new_id(), **payload}).status_code == 422
        assert client.get(f'/api/v1/evaluations/runs/{new_id()}').status_code == 404
        assert client.get('/api/v1/evaluations/runs/not-a-valid-id').status_code == 404


def test_cooperative_timeout_never_exports_success(evaluation, monkeypatch):
    from importlib import import_module

    c, _, command = evaluation
    c.settings.evaluation_run_timeout_seconds = 120
    clock = [0.]
    original = c.search.search
    def slow(*args, **kwargs):
        result = original(*args, **kwargs)
        clock[0] += 121
        return result
    monkeypatch.setattr(import_module('data_sync_etl.application.evaluation'), 'perf_counter', lambda: clock[0])
    c.search.search = slow
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        result = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id}).json()
        assert result['status'] == 'FAILED' and 'EVALUATION_TIMEOUT' in result['warnings']
        assert client.get(f'/api/v1/evaluations/runs/{result["run_id"]}/report').status_code == 409


def test_stream_counts_real_work_including_warmup_and_only_finishes_after_save(evaluation):
    c, _, command = evaluation
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        response = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id, 'warmup_queries': 1},
                               headers={'Accept': 'application/x-ndjson'})
        assert response.status_code == 201 and 'application/x-ndjson' in response.headers['content-type']
        events = [json.loads(line) for line in response.text.splitlines()]
        assert [event['completed'] for event in events[:-1]] == list(range(10))
        assert all(event['total'] == 9 and event['percent'] < 100 for event in events[:-1])
        assert sum(event.get('phase') == 'warmup' for event in events) == 3
        final = events[-1]
        assert final['percent'] == 100 and final['result']['status'] == 'COMPLETED'
        assert 'run' not in final and 'queries' not in final
        saved = c.evaluations.store.get('runs', final['result']['run_id'])
        assert saved['status'] == 'COMPLETED' and len(saved['observations']) == 6
        assert client.post('/api/v1/evaluations/runs', json={'dataset_id': new_id()},
                           headers={'Accept': 'application/x-ndjson'}).status_code == 404


def test_stream_storage_failure_cannot_report_100(evaluation):
    c, _, command = evaluation
    original = c.evaluations.store.put
    def fail_save(kind, payload, *, replace=False):
        if replace:
            raise EvaluationError('EVALUATION_STORAGE_UNAVAILABLE', 503)
        return original(kind, payload)
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        c.evaluations.store.put = fail_save
        response = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id},
                               headers={'Accept': 'application/x-ndjson'})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]['type'] == 'error' and events[-1]['status'] == 503
        assert not any(event.get('percent') == 100 or event['type'] == 'result' for event in events)


def test_disconnect_closes_run_and_releases_lock(evaluation):
    from data_sync_etl.api.evaluation_schemas import DatasetRequest, RunRequest
    from data_sync_etl.domain.search_access import SearchAccess

    c, _, command = evaluation
    access = SearchAccess(scope='local_demo')
    dataset = c.evaluations.create_dataset(DatasetRequest(**command).model_dump(), access)
    events = c.evaluations.run_events(RunRequest(dataset_id=dataset['dataset_id']).model_dump(), access)
    first = next(events)
    events.close()
    saved = c.evaluations.store.get('runs', first['run_id'])
    assert saved['status'] == 'FAILED' and 'EVALUATION_CANCELLED' in saved['warnings']
    with c.evaluations.store.lock('run'):
        pass


def test_dataset_without_confirmation_is_saved_without_claiming_ground_truth(evaluation):
    c, _, command = evaluation
    command.pop('ground_truth_confirmed')
    with api(c) as client:
        response = client.post('/api/v1/evaluations/datasets', json=command)
        assert response.status_code == 201
        assert response.json()['validation']['ground_truth_confirmed'] is False
        assert 'LABELS_NOT_HUMAN_CONFIRMED' in response.json()['validation']['warnings']


def test_full_corpus_checked_only_at_run_boundaries(evaluation, monkeypatch):
    c, _, command = evaluation
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        calls = []
        original = c.evaluations.corpus
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        monkeypatch.setattr(c.evaluations, 'corpus', counted)
        result = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id}).json()
        assert result['status'] == 'COMPLETED'
        assert len(calls) == 2  # Not 2 * query_count * method_count + boundaries.


@pytest.mark.parametrize('change', ['unit_access', 'index_run', 'source_hash', 'model', 'threshold',
    'meaning_mode', 'meaning_threshold', 'meaning_fallback', 'meaning_floor', 'meaning_context_floor',
    'meaning_context_margin', 'bow_coverage'])
def test_light_marker_detects_access_index_source_and_model_changes(evaluation, change):
    c, version, command = evaluation
    original = c.search.search
    calls = []
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        with c.uow() as repo:
            if change == 'unit_access':
                unit = repo.find('content_units', {'resource_version_id': version['id']})[0]
                repo.update('content_units', unit['id'], {'review_status': 'REJECTED'})
            elif change == 'index_run':
                run = c.indexing._runs(repo, version['id'])[0]
                repo.update('etl_stage_runs', run['id'], {'status': 'FAILED'})
            elif change == 'source_hash':
                repo.update('resource_versions', version['id'], {'source_hash': 'f' * 64})
            elif change == 'model':
                c.indexing.embedding.version = 'changed-model'
            elif change == 'threshold':
                c.settings.search_vector_min_similarity = 0.75
            elif change == 'meaning_mode':
                c.settings.index_meaning_bags_enabled = True
            elif change == 'meaning_threshold':
                c.settings.index_meaning_group_threshold = 0.9
            elif change == 'meaning_fallback':
                c.settings.search_meaning_context_fallback_enabled = True
            elif change == 'meaning_floor':
                c.settings.search_meaning_fallback_min_similarity = .57
            elif change == 'meaning_context_floor':
                c.settings.search_meaning_fallback_min_context_similarity = .54
            elif change == 'meaning_context_margin':
                c.settings.search_meaning_context_margin = .04
            else:
                c.settings.search_bow_min_coverage = .3
        return result
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        c.search.search = changed
        result = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id}).json()
        assert result['status'] == 'NOT_COMPARABLE' and 'EVALUATION_CORPUS_CHANGED' in result['warnings']
        assert len(calls) == 1
        stored = c.evaluations.store.get('runs', result['run_id'])
        assert stored['summary'] == {} and all(not row['metrics'] for row in stored['observations'])


def test_final_full_validation_detects_mongo_artifact_change(evaluation):
    c, _, command = evaluation
    original = c.search.search
    calls = []
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(1)
        if len(calls) == 6:
            # Valid dimension, different vector: PG markers intentionally do not read embeddings.
            bag = next(iter(c.indexing.bags.rows.values()))
            bag.embedding = [0.3, 0.2, 0.1]
        return result
    with api(c) as client:
        dataset_id = client.post('/api/v1/evaluations/datasets', json=command).json()['dataset_id']
        c.search.search = changed
        result = client.post('/api/v1/evaluations/runs', json={'dataset_id': dataset_id}).json()
        assert result['status'] == 'NOT_COMPARABLE'
        assert c.evaluations.store.get('runs', result['run_id'])['summary'] == {}


def test_marker_uses_batched_queries_without_text_or_mongo(evaluation):
    from sqlalchemy import event

    from data_sync_etl.domain.search_access import SearchAccess

    c, _, _ = evaluation
    statements = []
    def recorded(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lower())
    event.listen(c.engine, 'before_cursor_execute', recorded)
    try:
        marker = c.evaluations.corpus_marker({}, SearchAccess('local_demo'))
    finally:
        event.remove(c.engine, 'before_cursor_execute', recorded)
    assert len(marker) == 64 and len(statements) == 6
    assert all('content_unit_texts' not in statement for statement in statements)
