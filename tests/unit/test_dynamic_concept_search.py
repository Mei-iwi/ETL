"""Actual BoW/index/HTTP contracts with offline reviewed corpus mappings."""
import pytest

from data_sync_etl.api.search_schemas import SearchResponse
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient
from tests.unit.test_dynamic_concept_dictionary import (
    activate,
    approve,
    resource,
)
from tests.unit.test_dynamic_concept_dictionary import pipeline as pipeline
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('searchable', [{'texts': ['Cache makes repeated data lookups faster.']}], indirect=True)
def test_actual_bow_before_after_seed_parity_and_http(searchable, pipeline):
    c, _ = searchable
    inventory, store, worker, candidate = pipeline
    before = c.search.search('bộ nhớ đệm', 'bow')
    assert before['total'] == 0
    approve(worker, candidate)
    provider = activate(inventory, store, worker)
    c.search.concept_resolver = provider
    c.indexing.embedding.prepare = lambda: pytest.fail('BoW loaded model')
    after = c.search.search('bộ nhớ đệm', 'bow')
    assert after['total'] == 1
    SearchResponse.model_validate(after)
    assert any(term['term'] == 'bộ nhớ đệm' for term in after['query_expansion']['terms'])
    assert 'Cache' in after['items'][0]['snippet']
    assert after['search_fingerprint'] != before['search_fingerprint']
    with TestClient(create_app(c)) as api:
        response = api.post('/api/v1/search', json={'query': 'bộ nhớ đệm', 'method': 'bow'})
        assert response.status_code == 200 and response.json()['total'] == 1
    provider.enabled = False
    disabled = c.search.search('bộ nhớ đệm', 'bow')
    assert disabled['items'] == before['items']
    assert disabled['search_fingerprint'] == before['search_fingerprint']


def test_pending_does_not_change_search_and_vector_is_not_expanded(searchable, pipeline, monkeypatch):
    c, _ = searchable
    inventory, store, worker, _ = pipeline
    provider = activate(inventory, store, worker)  # Empty approved release
    c.search.concept_resolver = provider
    baseline = c.search.search('page', 'bow')
    assert baseline['query_expansion']['dictionary_fingerprint'] == default_dictionary().fingerprint
    monkeypatch.setattr(provider, 'pin', lambda *args, **kwargs: pytest.fail('Vector was expanded'))
    captured = []
    monkeypatch.setattr(c.search, '_search', lambda query, *args, **kwargs: captured.append(query) or {'query': query})
    assert c.search.search('original mixed query', 'vector')['query'] == 'original mixed query'
    assert captured == ['original mixed query']


def test_mid_rank_revoke_recomputes_seed_and_never_serializes_old_alias(searchable, pipeline, monkeypatch):
    c, _ = searchable
    inventory, store, worker, candidate = pipeline
    approve(worker, candidate)
    c.search.concept_resolver = activate(inventory, store, worker)
    calls = []
    def ranking(*args, **kwargs):
        calls.append(c.search.dictionary.fingerprint)
        if len(calls) == 1:
            inventory.put(resource(revision=2, public=False))
        return {'terms': [term.term for term in c.search.dictionary.expand('cache').terms]}
    monkeypatch.setattr(c.search, '_search', ranking)
    result = c.search.search('cache', 'bow')
    assert len(calls) == 2 and calls[0] != calls[1]
    assert result['terms'] == []
