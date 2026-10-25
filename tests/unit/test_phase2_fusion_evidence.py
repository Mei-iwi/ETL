from copy import deepcopy

import pytest
from pydantic import ValidationError

from data_sync_etl.config import Settings
from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.evidence_selection import (
    EvidenceCandidate,
    bounded_evidence,
    select_evidence,
)
from data_sync_etl.domain.retrieval_fusion import (
    query_form,
    vector_priority_rrf,
    version_budget,
    version_rrf,
)
from data_sync_etl.domain.search import SearchError
from tests.unit.test_search import searchable as searchable
from tests.unit.test_semantic import semantic_search as semantic_search


def candidate(key, text, score, *, version='v', bag='b'):
    return EvidenceCandidate(version, bag, {'id': key, 'content_unit_id': 'unit-' + key,
        'unit_chunk_sequence': 1, 'char_start': 0, 'char_end': len(text), 'text': text}, score)


def test_weighted_rrf_uses_ranks_and_best_version_once_including_single_branch():
    branches = {'bow': {'a': 999., 'a2': 998., 'b': 1.}, 'vector': {'b': .99, 'a': .1, 'c': .05}}
    versions = {'a': 'A', 'a2': 'A', 'b': 'B', 'c': 'C'}
    default = version_rrf(branches, versions)
    equal = version_rrf(branches, versions, weights={'bow': 1., 'vector': 1.})
    assert default == equal
    result = version_rrf(branches, versions, weights={'bow': .5, 'vector': 1.})
    assert result.scores['b'] == pytest.approx(.5 / 62 + 1 / 61)
    assert result.scores['a'] == result.scores['a2'] == pytest.approx(.5 / 61 + 1 / 62)
    assert result.scores['c'] == pytest.approx(1 / 63)
    assert result.components['A'] == {'bow': 999., 'vector': .1}
    assert version_budget(branches['bow'], versions, 2) == {'a': 999., 'b': 1.}
    assert len(version_budget(branches['bow'], versions, 99)) == 2
    for weights in ({'bow': .5}, {'bow': 0., 'vector': 1.}, {'bow': float('nan'), 'vector': 1.},
                    {'bow': True, 'vector': 1.}):
        with pytest.raises(ValueError):
            version_rrf(branches, versions, weights=weights)
    with pytest.raises(KeyError):
        version_budget({'unknown': 1.}, {}, 1)


@pytest.mark.parametrize('query,form', [('HL26_XDRV_R012', 'identifier'), ('C++', 'technical'),
    ('C#', 'technical'), ('SQL', 'acronym'), ('UCLN', 'acronym'), ('Comment calculer ceci ?', 'question'),
    ('How plants make food?', 'question'), ('lá xanh', 'short')])
def test_query_form_is_observable_rule_not_language_confidence(query, form):
    assert query_form(query) == form


def test_vector_priority_protects_technical_lexical_and_retains_lexical_only():
    branches = {'bow': {'code': 5., 'only': 4.}, 'vector': {'dense': .9, 'code': .3}}
    versions = {k: k for k in ('code', 'only', 'dense')}
    assert vector_priority_rrf('C++', branches, versions) == version_rrf(branches, versions)
    result = vector_priority_rrf('How does this work?', branches, versions)
    assert result.scores['dense'] > result.scores['code'] > result.scores['only']


def test_selector_separates_version_ranking_from_real_near_tie_evidence_and_locator():
    rows = [candidate('topic', 'Cây xanh phát triển dưới ánh sáng.', .81, bag='b1'),
            candidate('answer', 'Giấy lọc không tách được muối hòa tan khỏi nước.', .80, bag='b2')]
    before = deepcopy(rows)
    dictionary = default_dictionary()
    query = 'Giấy lọc không tách được muối hòa tan'
    assert select_evidence(rows, version_id='v').chunk['id'] == 'topic'
    selected = select_evidence(rows, version_id='v', strategy='context', query=query,
        keywords=query.split(), dictionary=dictionary, expansion=dictionary.expand(query))
    assert selected.chunk['id'] == 'answer' and selected.bag_id == 'b2'
    assert selected.chunk['content_unit_id'] == 'unit-answer' and rows == before
    assert select_evidence(rows, version_id='v', strategy='context', margin=0., query=query,
        keywords=query.split()).chunk['id'] == 'topic'
    assert select_evidence([], version_id='v') is None
    assert len(bounded_evidence(rows, version_id='v', top_n=1)) == 1
    tied = [candidate('b', 'same', .8), candidate('a', 'same', .8)]
    assert select_evidence(tied, version_id='v').chunk['id'] == 'a'
    with pytest.raises(SearchError):
        select_evidence(rows, version_id='foreign')
    with pytest.raises(SearchError):
        select_evidence([rows[0], candidate('topic', 'changed', .9)], version_id='v')
    with pytest.raises(SearchError):
        select_evidence([candidate('bad', 'text', float('nan'))], version_id='v')


def test_experimental_flags_fail_closed_without_chunk_index_and_version_fusion():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, search_evidence_strategy='version_context')
    with pytest.raises(ValidationError):
        Settings(_env_file=None, search_hybrid_lexical_weight=.5)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, semantic_enabled=True, search_hybrid_fusion_unit='version',
                 semantic_rerank_enabled=True, search_evidence_strategy='version_context')


@pytest.mark.parametrize('method', ['vector', 'hybrid'])
def test_service_optin_evidence_keeps_version_score_provenance_and_context(semantic_search, method):
    c, version, service = semantic_search
    service.build(version['id'], apply=True)
    c.settings.semantic_enabled = True
    c.settings.search_hybrid_fusion_unit = 'version'
    c.settings.search_hybrid_lexical_weight = .5
    c.settings.search_evidence_strategy = 'version_context'
    result = c.search.search('page text', method)
    item = result['items'][0]
    assert item['resource_version_id'] == version['id']
    assert item['score_components']['vector'] == pytest.approx(1.)
    context = c.search.context(item['context_id'], neighbors=0)
    assert context['bag_id'] == item['bag_id']
    assert context['content_unit_id'] == item['content_unit_id']
    assert item['snippet'] in next(row['text'] for row in context['chunks'] if row['selected'])
    assert 'version_context' in result['ranking_algorithm']
    snapshot, metadata = service._snapshot(version['id'])
    evidence = []
    assert service.scores(snapshot, metadata, {}, set(), [1.] + [0.] * 1023,
                          lambda: None, [0], evidence_candidates=evidence) == {}
    assert evidence == []
    c.settings.search_evidence_top_n = 1
    service.scores(snapshot, metadata, c.indexing._roots(snapshot), {u['id'] for u in snapshot[2]},
                   [1.] + [0.] * 1023, lambda: None, [0], evidence_candidates=evidence)
    assert len(evidence) == 1 and evidence[0].version_id == version['id']
