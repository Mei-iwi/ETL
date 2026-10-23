from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_context import decode_context, encode_context
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('token', ['!', 'a'*257, 'W10', 'e30', 'null', '%%%%', 'eyJ4IjoxfQ'])
def test_malformed_tokens(token):
    with pytest.raises(SearchError) as error:
        decode_context(token)
    assert error.value.status == 404


@pytest.mark.parametrize('searchable', [False, True], indirect=True)
def test_context_matches_search_snippet_and_is_read_only(searchable):
    c, version = searchable
    result = c.search.search('page text', 'hybrid')['items'][0]
    before = deepcopy(c.documents.units), deepcopy(c.indexing.bags.rows)
    with c.uow() as repo:
        runs = repo.find('etl_stage_runs', {})
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        response = api.get(result['context_url'])
        assert response.status_code == 200, response.text
        data = response.json()
        selected = next(chunk for chunk in data['chunks'] if chunk['selected'])
        assert selected['text'].startswith(result['snippet'])
        assert data['resource_version_id'] == version['id']
        assert data['bag_id'] == result['bag_id']
        assert selected['content_unit_id'] == result['content_unit_id']
        assert len(data['heading_path']) == (4 if result['representation_mode'] == 'structured' else 1)
        assert 'storage_object_key' not in response.text and 'embedding' not in response.text
        assert api.get(result['context_url'] + '?neighbors=3').status_code == 422
        assert api.get('/api/v1/chunks/invalid/context').status_code == 404
    assert before == (c.documents.units, c.indexing.bags.rows)
    with c.uow() as repo:
        assert repo.find('etl_stage_runs', {}) == runs


@pytest.mark.parametrize('searchable', [{'texts': ['\n'.join(
    ' '.join(f'word{i}' for i in range(start, start + 10)) for start in range(0, 70, 10))]}], indirect=True)
def test_neighbors_offsets_and_ordinal_bounds(searchable):
    c, version = searchable
    item = c.search.search('word42', 'bow')['items'][0]
    version_id, unit_id, _, fingerprint = decode_context(item['context_id'])
    token = encode_context(version_id, unit_id, 2, fingerprint)
    context = c.search.context(token, neighbors=2)
    assert len(context['chunks']) == 4
    assert sum(chunk['selected'] for chunk in context['chunks']) == 1
    source = c.documents.units[unit_id]['text']
    for chunk in context['chunks']:
        assert chunk['text'] == source[chunk['char_start']:chunk['char_end']]
        assert chunk['page_from'] == item['page_from']
    assert len(c.search.context(token, neighbors=0)['chunks']) == 1
    bad = encode_context(version['id'], unit_id, 10000, fingerprint)
    with pytest.raises(SearchError) as error:
        c.search.context(bad)
    assert error.value.status == 404


@pytest.mark.parametrize('change', ['fingerprint', 'failed', 'unpublished', 'ineligible', 'missing_unit'])
def test_stale_and_revoked_context(searchable, change):
    c, version = searchable
    item = c.search.search('page', 'bow')['items'][0]
    version_id, unit_id, ordinal, fingerprint = decode_context(item['context_id'])
    token = item['context_id']
    if change == 'fingerprint':
        token = encode_context(version_id, unit_id, ordinal, 'a'*32)
    elif change == 'missing_unit':
        token = encode_context(version_id, 'missing', ordinal, fingerprint)
    else:
        with c.uow() as repo:
            if change == 'failed':
                repo.update('etl_stage_runs', c.indexing._runs(repo, version_id)[0]['id'], {'status': 'FAILED'})
            elif change == 'unpublished':
                repo.update('master_learning_resources', version['resource_id'], {'publication_status': 'DRAFT'})
            else:
                repo.update('content_units', unit_id, {'retrieval_eligible': False})
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        response = api.get(f'/api/v1/chunks/{token}/context')
        assert response.status_code == (409 if change == 'fingerprint' else 404)
        assert item['snippet'] not in response.text


def test_changed_current_text_returns_stale_not_old_content(searchable):
    c, _ = searchable
    item = c.search.search('page', 'bow')['items'][0]
    with c.uow() as repo:
        repo.update('content_unit_texts', item['content_unit_id'], {'text': 'New current content'})
    c.documents.units[item['content_unit_id']]['text'] = 'New current content'
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        response = api.get(item['context_url'])
        assert response.status_code == 409
        assert response.json()['detail']['code'] == 'CONTEXT_STALE'
        assert 'New current content' not in response.text


def test_openapi_search_contract_is_exposed(searchable):
    c, _ = searchable
    schema = create_app(c).openapi()
    for path, verb in [('/api/v1/search', 'post'), ('/api/v1/chunks/{context_id}/context', 'get')]:
        operation = schema['paths'][path][verb]
        assert operation['tags'] == ['Learning Resource Retrieval and Search']
        assert {'200', '404', '409', '422', '503'} <= operation['responses'].keys()


@pytest.mark.parametrize('searchable', [{'texts': [
    'the and for in the text\n' * 9 + 'A xylophone produces musical sounds.',
]}], indirect=True)
def test_context_selects_informative_late_hit_and_keeps_search_locator(searchable):
    c, _ = searchable
    item = c.search.search('please explain the xylophone in the text', 'bow')['items'][0]
    assert item['context_evidence'] == 'lexical'
    assert 'xylophone' in item['snippet']
    context = c.search.context(item['context_id'], neighbors=0)
    selected = next(chunk for chunk in context['chunks'] if chunk['selected'])
    assert 'xylophone' in selected['text']
    assert item['snippet'] in selected['text']
    assert selected['unit_chunk_sequence'] > 1


def test_vector_only_context_is_explicitly_a_bag_fallback(searchable):
    c, _ = searchable
    item = c.search.search('unmatchedsemanticquery', 'vector')['items'][0]
    assert item['context_evidence'] == 'bag'
    selected = c.search.context(item['context_id'], neighbors=0)['chunks'][0]
    assert item['snippet'] in selected['text']
