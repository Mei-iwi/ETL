import math
from copy import deepcopy
from functools import partial

import pytest
from conftest import completed
from fastapi.testclient import TestClient

from data_sync_etl.application.search import SearchService
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import binary_score, cosine, normalize_query, ranks, rrf
from data_sync_etl.domain.search_access import SearchAccess
from data_sync_etl.domain.search_context import decode_context, encode_context
from data_sync_etl.main import create_app
from tests.indexing_support import setup_index, structured_projection


@pytest.fixture
def searchable(container, request, monkeypatch):
    # Isolated fixtures attest a verified loopback Uvicorn launch; real runtime
    # acceptance checks the actual process separately.
    monkeypatch.setattr('sys.argv', ['uvicorn/__main__.py', 'app', '--host', '127.0.0.1', '--no-proxy-headers'])
    monkeypatch.setattr('data_sync_etl.api.search_access.container_runtime', lambda: False)
    container.settings.app_environment = 'development'
    container.settings.search_internal_dev_enabled = True
    container.settings.admin_enabled = True
    index = setup_index(container)
    options = getattr(request, 'param', False)
    options = options if isinstance(options, dict) else {'structured': options}
    container.settings.index_meaning_bags_enabled = options.get('meanings', False)
    if options.get('structured'):
        structured_projection(container)
    if options.get('texts'):
        container.settings.chunk_size_words = 20
        container.settings.chunk_overlap_words = 5
    original = container.postprocess.run_locked

    def reviewed(version_id, **kwargs):
        result = original(version_id, **kwargs)
        with container.uow() as repo:
            units = repo.find('content_units', {'resource_version_id': version_id}, order=('sequence_no',))
            if options.get('mixed'):
                repo.update('content_units', units[0]['id'], {'parent_unit_id': None})
            for unit in units:
                repo.update('content_units', unit['id'], {
                    'retrieval_eligible': not options.get('unreviewed', False),
                    'review_status': options.get('review_status', 'UNREVIEWED' if options.get('unreviewed') else 'APPROVED')})
            if options.get('rejected_leaf'):
                repo.update('content_units', units[0]['id'], {'review_status': 'REJECTED'})
            if options.get('restricted_parent'):
                for unit in units:
                    if unit['parent_unit_id'] is None:
                        repo.update('content_units', unit['id'], {'retrieval_eligible': False})
        return result

    container.postprocess.run_locked = reviewed
    version, _ = completed(container, texts=options.get('texts'))
    index.enqueue(version['id'])
    assert index.once()
    index.bags.search_candidates = lambda ids, model, limit: [
        deepcopy(bag) for bag in sorted(index.bags.rows.values(), key=lambda bag: bag.id)
        if bag.resource_version_id in ids and bag.embedding_model == model and bag.is_current][:limit]
    container.search = SearchService(index, container.settings, index.bags)
    container.search.search = partial(container.search.search, access=SearchAccess('local_demo'))
    container.search.context = partial(container.search.context, access=SearchAccess('local_demo'))
    return container, version


def test_scores_and_normalization():
    assert normalize_query('  Vie\u0323̂t  ') == 'Việt'
    score, matched = binary_score(['học', 'liệu'], ['học', 'toán'])
    assert score == .5 and matched == ['học']
    assert binary_score([], ['học']) == (0., [])
    assert cosine([1., 0., 0.], [0., 1., 0.]) == 0
    assert cosine([1., 0.], [-1., 0.]) == -1
    assert cosine([1e308, 1e308], [1e308, 1e308]) == pytest.approx(1.)
    assert ranks({'b': 1., 'a': 1.}) == {'a': 1, 'b': 2}
    assert rrf({'a': 1.}, {'b': 1., 'a': .5})['a'] == pytest.approx(1/61 + 1/62)


@pytest.mark.parametrize('bad', [[0., 0.], [math.nan, 1.], [1.], [math.inf, 1.]])
def test_invalid_cosine(bad):
    with pytest.raises(IndexingError):
        cosine([1., 0.], bad)


def test_context_locator_round_trip():
    token = encode_context('version', 'unit', 1, 'a'*64)
    assert decode_context(token) == ('version', 'unit', 1, 'a'*32)


@pytest.mark.parametrize('body', [
    {'query': ' '}, {'query': 'x'*4001}, {'query': 1}, {'query': 'x', 'method': 'bm25'},
    {'query': 'x', 'size': True}, {'query': 'x', 'size': 51}, {'query': 'x', 'page': 0},
    {'query': 'x', 'page': 10001, 'size': 50}, {'query': 'x', 'extra': 1},
    {'query': 'x', 'filters': {'subject_id': 'x'*31}},
])
def test_api_validation(searchable, body):
    c, _ = searchable
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        response = api.post('/api/v1/search', json=body)
        assert response.status_code == 422
        assert response.json()['detail']['code'] == 'REQUEST_VALIDATION_ERROR'


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_api_ranking_grouping_filters_and_read_only(searchable, method):
    c, version = searchable
    before = deepcopy(c.documents.units), deepcopy(c.indexing.bags.rows)
    with c.uow() as repo:
        runs = repo.count('etl_stage_runs', {})
        resources = repo.count('master_learning_resources', {})
        subjects = repo.find('resource_subjects', {'resource_id': version['resource_id']})
        grades = repo.find('resource_grade_levels', {'resource_id': version['resource_id']})
        resource = repo.get('master_learning_resources', version['resource_id'])
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        payload = {'query': 'page text', 'method': method}
        result = api.post('/api/v1/search', json=payload)
        assert result.status_code == 200, result.text
        body = result.json()
        assert body['total'] == 1 and body['items'][0]['matched_bag_count'] == 3
        item = body['items'][0]
        assert item['resource_version_id'] == version['id'] and item['snippet'].startswith('Page')
        assert item['representation_mode'] == 'fallback'
        assert 'embedding' not in item and 'storage_object_key' not in item
        assert decode_context(item['context_id'])[0] == version['id']
        for key, value in [('subject_id', subjects[0]['subject_id']),
                           ('grade_level_id', grades[0]['grade_level_id']),
                           ('resource_type_code', resource['resource_type_code'])]:
            assert api.post('/api/v1/search', json={**payload, 'filters': {key: value}}).json()['total'] == 1
            assert api.post('/api/v1/search', json={**payload, 'filters': {key: 'missing'}}).json()['total'] == 0
        assert api.post('/api/v1/search', json={**payload, 'page': 2, 'size': 1}).json()['items'] == []
    assert before == (c.documents.units, c.indexing.bags.rows)
    with c.uow() as repo:
        assert repo.count('etl_stage_runs', {}) == runs
        assert repo.count('master_learning_resources', {}) == resources


@pytest.mark.parametrize('change', ['failed', 'pending', 'unpublished', 'inactive', 'stale', 'ineligible', 'model'])
def test_guards(searchable, change):
    c, version = searchable
    with c.uow() as repo:
        if change in ('failed', 'pending'):
            run = c.indexing._runs(repo, version['id'])[0]
            repo.update('etl_stage_runs', run['id'], {'status': change.upper()})
        elif change in ('unpublished', 'inactive'):
            repo.update('master_learning_resources', version['resource_id'],
                        {'publication_status': 'DRAFT'} if change == 'unpublished' else {'is_active': False})
        elif change == 'ineligible':
            for row in repo.find('content_units', {'resource_version_id': version['id']}):
                repo.update('content_units', row['id'], {'retrieval_eligible': False})
    if change == 'stale':
        next(iter(c.documents.units.values()))['text'] = 'changed'
    if change == 'model':
        next(iter(c.indexing.bags.rows.values())).embedding_model = 'other@revision'
    assert c.search.search('page', 'bow')['total'] == 0


def test_no_keywords_no_match_model_error_and_cap(searchable):
    c, _ = searchable
    assert c.search.search('và của', 'bow')['message'] == 'QUERY_NO_KEYWORDS'
    assert c.search.search('missingword', 'bow')['total'] == 0
    c.indexing.embedding.embed = lambda _: (_ for _ in ()).throw(IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503))
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'vector'}).status_code == 503
        assert api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'}).status_code == 200
        c.settings.search_max_bags = 1
        response = api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'})
        assert response.status_code == 422 and response.json()['detail']['code'] == 'SEARCH_LIMIT_EXCEEDED'


@pytest.mark.parametrize('searchable', [True], indirect=True)
def test_structured_membership(searchable):
    c, version = searchable
    body = c.search.search('page text', 'hybrid')
    assert body['total'] == 1 and body['items'][0]['representation_mode'] == 'structured'


def test_pagination_after_grouping_and_latest_version_policy(searchable):
    from conftest import make_pdf

    c, _ = searchable
    other = c.ingestion.run('resource-2', 'input', 'sample.pdf')['version']
    c.ocr.create_job(other['id'])
    while c.worker.once('search-test'):
        pass
    c.indexing.enqueue(other['id'])
    assert c.indexing.once()
    first = c.search.search('page', 'bow', size=1)
    second = c.search.search('page', 'bow', size=1, page=2)
    assert first['total'] == second['total'] == 2
    assert first['items'][0]['resource_id'] != second['items'][0]['resource_id']
    make_pdf(c, texts=['Page newer text'], name='new.pdf')
    c.ingestion.run('resource-2', 'input', 'new.pdf')
    # A newer unprocessed eligible lifecycle version hides the older default hit.
    assert c.search.search('page', 'bow')['total'] == 1
    assert c.search.search('page', 'bow', filters={'resource_version_id': other['id']})['total'] == 1


def test_timeout_and_adapter_failure_are_safe(searchable):
    c, _ = searchable
    from data_sync_etl.domain.search import SearchError
    c.search._deadline = lambda started: (_ for _ in ()).throw(SearchError('SEARCH_TIMEOUT'))
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        response = api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'})
        assert response.status_code == 503 and response.json()['detail']['code'] == 'SEARCH_TIMEOUT'
        c.indexing.bags.ensure_ready = lambda: (_ for _ in ()).throw(RuntimeError('mongodb://secret'))
        response = api.post('/api/v1/search', json={'query': 'page', 'method': 'bow'})
        assert response.status_code == 503 and 'secret' not in response.text


@pytest.mark.parametrize('searchable', [{'structured': True, 'mixed': True}], indirect=True)
def test_mixed_bags_do_not_double_count_resource(searchable):
    c, version = searchable
    assert c.indexing.status(version['id'])['representation_mode'] == 'mixed'
    result = c.search.search('page', 'hybrid')
    assert result['total'] == 1 and result['items'][0]['matched_bag_count'] == 2


def test_mongo_selector_is_bounded_and_read_only():
    from unittest.mock import MagicMock

    from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore

    database = MagicMock()
    database.with_options.return_value = database
    cursor = MagicMock()
    database.content_bags.find.return_value = cursor
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.max_time_ms.return_value = cursor
    cursor.__iter__.return_value = iter([])
    assert MongoBagStore(database, 384).search_candidates(['version'], model='model@revision', limit=2001) == []
    selector, projection = database.content_bags.find.call_args.args
    assert selector == {'resource_version_id': {'$in': ['version']}, 'is_current': True, 'embedding_model': 'model@revision'}
    assert projection['embedding'] == 1
    cursor.limit.assert_called_once_with(2001)
    cursor.max_time_ms.assert_called_once_with(5000)
    database.content_bags.update_one.assert_not_called()
    database.create_collection.assert_not_called()


@pytest.mark.parametrize('warmup,query_time,code', [
    (20, 0, None), (61, 0, 'SEARCH_MODEL_WARMUP_TIMEOUT'), (20, 16, 'SEARCH_TIMEOUT'),
])
def test_cold_model_has_separate_bounded_budget(searchable, monkeypatch, warmup, query_time, code):
    from importlib import import_module

    from data_sync_etl.domain.search import SearchError

    c, _ = searchable
    clock = [0.0]
    monkeypatch.setattr(import_module('data_sync_etl.application.search'), 'monotonic', lambda: clock[0])
    def prepare():
        clock[0] += warmup
    original = c.indexing.embedding.embed
    def embed(text):
        clock[0] += query_time
        return original(text)
    c.indexing.embedding.prepare = prepare
    c.indexing.embedding.embed = embed
    if code:
        with pytest.raises(SearchError) as error:
            c.search.search('page', 'hybrid')
        assert error.value.code == code
    else:
        assert c.search.search('page', 'hybrid')['total'] == 1
