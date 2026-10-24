from copy import deepcopy

import pytest

from data_sync_etl.application.lexical_export import TrustedLexicalExporter, verify_export
from data_sync_etl.application.search_evaluation_snapshot import replay_lexical_snapshot
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_search import searchable as searchable
from tests.unit.test_search_visible_hydration import second_resource


def exporter(c, provider=None):
    return TrustedLexicalExporter(c.search, provider or (lambda: SearchAccess('authenticated_admin', actor_id='test-admin')),
                                   environment='isolated_fixture')


def test_trusted_export_full_corpus_runtime_bm25_pagination_parity_and_stats(searchable):
    c, _ = searchable
    second_resource(c)
    payload = exporter(c).capture()
    snapshot = verify_export(payload)
    assert payload['statistics']['document_count'] == len(snapshot.bags) >= 2
    assert payload['statistics']['total_length'] == sum(b.lexical.length for b in snapshot.bags)
    assert all(payload['root_units'].values())
    access = SearchAccess('authenticated_admin', actor_id='test-admin')
    for query in ('page text', 'PAGE TEXT', 'absent-word', 'SQL', 'C++', 'page page text'):
        scores = replay_lexical_snapshot(query, snapshot)
        resource_scores = {}
        for bag in snapshot.bags:
            if bag.bag_id in scores:
                resource_scores[bag.resource_id] = max(resource_scores.get(bag.resource_id, 0), scores[bag.bag_id])
        expected = sorted(resource_scores, key=lambda r: (-resource_scores[r], r))
        for page in (1, 2, 3):
            result = c.search.search(query, 'bow', size=1, page=page, access=access,
                                     _batch_texts=True, _batch_bags=True)
            assert result['total'] == len(expected)
            assert [i['resource_id'] for i in result['items']] == expected[page-1:page]
            assert all(i['score'] == pytest.approx(resource_scores[i['resource_id']]) for i in result['items'])
    tampered = deepcopy(payload)
    tampered['statistics']['document_count'] += 1
    tampered['checksum'] = digest({k: v for k, v in tampered.items() if k != 'checksum'})
    with pytest.raises(ValueError, match='corpus/mapping'):
        verify_export(tampered)


@pytest.mark.parametrize('access', [SearchAccess(), SearchAccess('local_demo'),
    SearchAccess('authenticated_admin'), SearchAccess('authenticated_user', actor_id='no-grants')])
def test_export_refuses_untrusted_or_ungranted_scope(searchable, access):
    with pytest.raises(SearchError):
        exporter(searchable[0], lambda: access).capture()


def test_export_user_scope_filters_entire_bags_and_nonhits(searchable):
    c, first = searchable
    second_resource(c)
    access = SearchAccess('authenticated_user', frozenset([first['resource_id']]), actor_id='reviewer')
    snapshot = verify_export(exporter(c, lambda: access).capture())
    assert {b.resource_id for b in snapshot.bags} == {first['resource_id']}
    review = exporter(c, lambda: access).capture(include_review_candidates=True)
    assert review['review_candidates']
    assert {row['resource_id'] for row in review['review_candidates']} == {first['resource_id']}
    assert all(row['version_id'] == first['id'] for row in review['review_candidates'])
    verify_export(review)


def test_export_reauthorizes_before_exclusive_write_and_dry_run(searchable, tmp_path, monkeypatch):
    c, _ = searchable
    calls = []
    def revoked():
        calls.append(1)
        return SearchAccess('authenticated_admin', actor_id='actor') if len(calls) == 1 else SearchAccess()
    with pytest.raises(SearchError):
        exporter(c, revoked).capture()
    assert exporter(c).run(dry_run=True)['dry_run']
    calls.clear()
    def revoked_before_write():
        calls.append(1)
        return SearchAccess('authenticated_admin', actor_id='actor') if len(calls) <= 2 else SearchAccess()
    with pytest.raises(SearchError):
        exporter(c, revoked_before_write).run('.test-tmp/search-hybrid-phase3/refused-export.json')
    assert not __import__('pathlib').Path('.test-tmp/search-hybrid-phase3/refused-export.json').exists()
    with pytest.raises(ValueError):
        exporter(c).run(tmp_path/'public.json')
    original = c.search._snippets
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        with c.uow() as repo:
            repo.update('master_learning_resources', searchable[1]['resource_id'], {'is_active': False})
        return result
    monkeypatch.setattr(c.search, '_snippets', change)
    with pytest.raises(SearchError):
        exporter(c).capture()
