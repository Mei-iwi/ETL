"""Planner integration uses real isolated retrieval with model doubles only."""
import pytest

from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
@pytest.mark.parametrize('searchable', [{'texts': ['Cách giải phương trình bậc hai.']}], indirect=True)
def test_plan_is_opt_in_and_retains_original_and_version_identity(searchable, method):
    c, version = searchable
    query = 'Hãy giúp tôi tìm tài liệu về cách giải phương trình bậc hai.'
    baseline = c.search.search(query, method)
    assert 'query_plan' not in baseline
    c.settings.search_query_understanding_enabled = True
    result = c.search.search(query, method)
    from data_sync_etl.api.search_schemas import SearchResponse
    assert SearchResponse.model_validate(result).query == query
    assert result['query'] == query
    assert result['query_plan']['core_query'] == 'cách giải phương trình bậc hai'
    assert result['search_fingerprint'] != baseline['search_fingerprint']
    assert result['retrieval_diagnostics']['semantic_queries'] in ([], [query])
    assert len(result['items']) == len({row['resource_id'] for row in result['items']})
    assert all(item['resource_version_id'] == version['id'] for item in result['items'])


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_planner_never_expands_resource_grants(searchable, method):
    c, _ = searchable
    c.settings.search_query_understanding_enabled = c.settings.search_query_decomposition_enabled = True
    c.settings.search_query_semantic_variants_enabled = True
    result = c.search.search('Hãy tìm tài liệu về định luật vật lý và ứng dụng', method,
        access=SearchAccess('authenticated_user', actor_id='no-grant'))
    assert result['items'] == []


def test_planner_validation_failure_falls_back_but_vector_runtime_failure_is_not_hidden(searchable, monkeypatch):
    from data_sync_etl.domain.indexing import IndexingError
    c, _ = searchable
    c.settings.search_query_understanding_enabled = True
    def invalid(_):
        raise ValueError('invalid representation')
    monkeypatch.setattr('data_sync_etl.application.search.plan_query', invalid)
    result = c.search.search('quang hợp', 'bow')
    assert result['retrieval_diagnostics']['planner_status'] == 'FALLBACK_ORIGINAL'
    def broken(_):
        raise IndexingError('BLOCKED_EMBEDDING_RUNTIME', 503)
    monkeypatch.setattr(c.indexing.embedding, 'embed', broken)
    with pytest.raises(IndexingError, match='BLOCKED_EMBEDDING_RUNTIME'):
        c.search.search('quang hợp', 'vector')


@pytest.mark.parametrize('searchable', [{'texts': ['Mạng tinh thể.']}], indirect=True)
def test_semantic_variants_are_bounded_deduplicated_and_use_max_score(searchable, tmp_path, monkeypatch):
    from tests.corpus_dictionary_support import reviewed_dictionary
    c, _ = searchable
    encoded = []
    monkeypatch.setattr(c.indexing.embedding, 'embed', lambda text: (encoded.append(text) or [.1, .2, .3]))
    with reviewed_dictionary(tmp_path, 'Mạng tinh thể viết không dấu là mang tinh the', 'mang tinh the') as dictionary:
        c.search.dictionary = dictionary
        c.settings.search_query_understanding_enabled = True
        c.settings.search_approved_accent_variants_enabled = True
        baseline = c.search.search('mang tinh the', 'vector')
        encoded.clear()
        c.settings.search_query_semantic_variants_enabled = True
        result = c.search.search('mang tinh the', 'vector')
        assert encoded == ['mang tinh the', 'mạng tinh thể']
        assert result['total'] == baseline['total'] == 1
        assert result['items'][0]['score'] == baseline['items'][0]['score']
        assert set(result['items'][0]['score_components']) == {'vector'}


def test_planner_keeps_final_snapshot_fence(searchable, monkeypatch):
    from copy import deepcopy
    from data_sync_etl.domain.search import SearchError
    c, _ = searchable
    c.settings.search_query_understanding_enabled = c.settings.search_query_decomposition_enabled = True
    calls = []
    def phase(version_ids, **kwargs):
        calls.append(tuple(version_ids))
        if len(calls) == 2:
            next(iter(c.indexing.bags.rows.values())).embedding[0] += .123
        observed = deepcopy(c.indexing.bags.rows)
        return lambda ids, **kw: {key: observed.get(key) for key in ids}
    monkeypatch.setattr(c.indexing.bags, 'search_phase', phase, raising=False)
    with pytest.raises(SearchError, match='SEARCH_INDEX_STALE'):
        c.search.search('Hãy tìm tài liệu về page text', 'bow', _batch_texts=True, _batch_bags=True)
    assert len(calls) == 2


def test_experimental_flags_default_off_and_invalid_combinations_are_rejected():
    from pydantic import ValidationError
    from data_sync_etl.config import Settings
    settings = Settings(_env_file=None)
    assert not any((settings.search_query_understanding_enabled, settings.search_query_decomposition_enabled,
                    settings.search_query_semantic_variants_enabled, settings.search_query_long_semantic_enabled))
    with pytest.raises(ValidationError):
        Settings(_env_file=None, search_query_semantic_variants_enabled=True)


def test_query_variants_use_distinct_vector_cache_keys_and_live_permissions(searchable):
    from tests.search_query_cache_support import configure
    c, version = searchable
    model = configure(c, version)
    c.settings.search_query_understanding_enabled = True
    access = SearchAccess('local_demo')
    query = 'Hãy tìm tài liệu về page text'
    first = c.search.search_http(query, 'vector', access=access)
    before = model.encodes
    c.settings.search_query_semantic_variants_enabled = True
    second = c.search.search_http(query, 'vector', access=access)
    assert model.encodes == before + 1  # Original hit, separate core cache entry.
    assert first['search_fingerprint'] != second['search_fingerprint']
    assert c.search.search_http(query, 'vector', access=access) == second
    assert model.encodes == before + 1
    with c.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
    assert c.search.search_http(query, 'vector', access=access)['items'] == []


@pytest.mark.parametrize('searchable', [{'texts': ['Cách giải phương trình bậc hai.']}], indirect=True)
def test_core_lexical_branch_uses_same_corpus_and_maximum_not_added_scores(searchable):
    from data_sync_etl.domain.lexical import bm25
    c, _ = searchable
    c.settings.search_query_understanding_enabled = True
    query = 'Hãy tìm tài liệu về cách giải phương trình bậc hai'
    result = c.search.search(query, 'bow')
    assert result['total'] == 1
    documents = {key: bag.lexical for key, bag in c.indexing.bags.rows.items()}
    raw = bm25(query, documents, dictionary=c.search.dictionary, policy=c.settings.search_expansion)
    core = bm25(result['query_plan']['core_query'], documents, dictionary=c.search.dictionary,
                policy=c.settings.search_expansion)
    key = result['items'][0]['bag_id']
    expected = max(raw.get(key, 0), c.settings.search_expansion.synonyms * core.get(key, 0))
    assert result['items'][0]['score'] == pytest.approx(expected)
