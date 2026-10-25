import pytest

from data_sync_etl.config import Settings
from data_sync_etl.domain.retrieval_fusion import version_rrf
from data_sync_etl.domain.search import rrf
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_search import searchable as searchable


def test_different_bags_in_same_version_combine_without_duplicate_amplification():
    branches = {'bow': {'a1': 9., 'b1': 8., 'a2': 7.}, 'vector': {'a2': .9, 'b1': .8}}
    versions = {'a1': 'A', 'a2': 'A', 'b1': 'B'}
    legacy = rrf(*branches.values())
    assert legacy['b1'] > legacy['a1']  # Existing bag fusion misses cross-bag agreement.
    result = version_rrf(branches, versions)
    assert result.scores['a1'] == result.scores['a2'] == pytest.approx(2 / 61)
    assert result.scores['a1'] > result.scores['b1']
    assert result.components['A'] == {'bow': 9., 'vector': .9}
    assert result.representatives['A'] == 'a2'
    for i in range(100):
        branches['bow'][f'dup{i}'] = 6.
        versions[f'dup{i}'] = 'A'
    again = version_rrf(branches, versions)
    assert again.components == result.components
    assert again.scores['b1'] == result.scores['b1']


def test_single_branch_empty_branches_and_identity_contract():
    assert version_rrf({}, {}).scores == {}
    result = version_rrf({'bow': {}, 'vector': {'chunk': .8}}, {'chunk': 'v'})
    assert result.scores == {'chunk': pytest.approx(1 / 61)}
    assert result.representatives == {'v': 'chunk'}
    with pytest.raises(KeyError):
        version_rrf({'bow': {'unknown': 1.}}, {})
    assert Settings(_env_file=None).search_hybrid_fusion_unit == 'bag'


def test_service_experimental_fusion_preserves_context_pagination_and_stale_guard(searchable):
    c, version = searchable
    baseline = c.search.search('page text', 'hybrid')
    c.settings.search_hybrid_fusion_unit = 'version'
    result = c.search.search('page text', 'hybrid')
    assert result['total'] == baseline['total'] == 1
    assert result['ranking_algorithm'] == 'resource-version-rrf-v1'
    assert result['search_fingerprint'] != baseline['search_fingerprint']
    item = result['items'][0]
    assert item['resource_version_id'] == version['id']
    assert item['matched_bag_count'] == baseline['items'][0]['matched_bag_count']
    assert c.search.context(item['context_id'], neighbors=0)['chunks'][0]['selected']
    assert c.search.search('page text', 'hybrid', page=2, size=1)['items'] == []
    next(iter(c.documents.units.values()))['text'] = 'concurrently changed'
    assert c.search.search('page text', 'hybrid')['items'] == []


def test_experimental_fusion_never_grants_public_or_unassigned_access(searchable, monkeypatch):
    c, _ = searchable
    c.settings.search_hybrid_fusion_unit = 'version'
    monkeypatch.setattr(c.indexing.embedding, 'embed', lambda *_: pytest.fail('Public cannot encode'))
    assert c.search.search('page', 'hybrid', access=SearchAccess())['items'] == []
    # Unassigned authenticated users cannot gain evidence through fusion either.
    c.indexing.embedding.embed = lambda _: [1., 0., 0.]
    access = SearchAccess('authenticated_user', owner_id='no-owner')
    assert c.search.search('page', 'hybrid', access=access)['items'] == []
