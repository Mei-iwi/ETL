"""Admission tests isolate cosine scores; they do not claim model quality."""

from importlib import import_module

import pytest
from pydantic import ValidationError

from data_sync_etl.config import Settings
from tests.unit.test_search import searchable as searchable

search_module = import_module('data_sync_etl.application.search')


@pytest.mark.parametrize('value', [0, -0.1, 1.01, float('nan'), float('inf')])
def test_threshold_configuration_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, search_vector_min_similarity=value)


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
def test_positive_but_weak_similarity_can_return_zero_results(searchable, monkeypatch, method):
    c, _ = searchable
    c.settings.search_vector_min_similarity = 0.5
    monkeypatch.setattr(search_module, 'cosine', lambda *_: 0.49)
    result = c.search.search('unmatchedquery', method)
    assert result['items'] == []
    assert result['total'] == result['total_pages'] == 0
    assert result['ready_bag_count'] == 3
    assert result['message'] == 'OK'


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
def test_strong_dense_only_match_does_not_require_lexical_overlap(searchable, monkeypatch, method):
    c, _ = searchable
    c.settings.search_vector_min_similarity = 0.5
    monkeypatch.setattr(search_module, 'cosine', lambda *_: 0.8)
    result = c.search.search('unmatchedquery', method)
    assert result['total'] == 1
    assert result['items'][0]['score_components']['vector'] == 0.8
    assert 'bow' not in result['items'][0]['score_components']


def test_hybrid_preserves_lexical_hit_but_rejects_weak_vector_vote(searchable, monkeypatch):
    c, _ = searchable
    c.settings.search_vector_min_similarity = 0.5
    monkeypatch.setattr(search_module, 'cosine', lambda *_: 0.2)
    result = c.search.search('page', 'hybrid')
    assert result['total'] == 1
    assert result['items'][0]['score_components']['bow'] > 0
    assert 'vector' not in result['items'][0]['score_components']
    assert c.search.search('page', 'vector')['total'] == 0
    assert c.search.search('page', 'bow')['total'] == 1


def test_threshold_boundary_is_inclusive_and_configurable(searchable, monkeypatch):
    c, _ = searchable
    monkeypatch.setattr(search_module, 'cosine', lambda *_: 0.6)
    c.settings.search_vector_min_similarity = 0.6
    assert c.search.search('unmatchedquery', 'vector')['total'] == 1
    c.settings.search_vector_min_similarity = 0.61
    assert c.search.search('unmatchedquery', 'vector')['total'] == 0


@pytest.mark.parametrize('searchable', [{'texts': ['the plants and the sunlight']}], indirect=True)
def test_hybrid_cannot_revive_rejected_dense_hit_with_only_function_words(searchable, monkeypatch):
    c, _ = searchable
    monkeypatch.setattr(search_module, 'cosine', lambda *_: 0.2)
    assert c.search.search('machine learning and the', 'hybrid')['total'] == 0


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
def test_filtering_precedes_resource_grouping_counts_and_pagination(searchable, monkeypatch, method):
    c, _ = searchable
    other = c.ingestion.run('resource-2', 'input', 'sample.pdf')['version']
    c.ocr.create_job(other['id'])
    while c.worker.once('vector-relevance-test'):
        pass
    c.indexing.enqueue(other['id'])
    assert c.indexing.once()
    c.settings.search_vector_min_similarity = 0.5
    for page in (1, 2):
        scores = iter([0.8, 0.2, 0.2, 0.2, 0.2, 0.2])
        monkeypatch.setattr(search_module, 'cosine', lambda *_, values=scores: next(values))
        result = c.search.search('unmatchedquery', method, size=1, page=page)
        assert result['total'] == result['total_pages'] == 1
        assert result['ready_bag_count'] == 6
        if page == 1:
            assert result['items'][0]['matched_bag_count'] == 1
        else:
            assert result['items'] == []
