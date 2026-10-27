"""Isolated rank/hydration counters, not live authority or BGE benchmarks."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from data_sync_etl.application.search_sessions import RankedHit, SearchResultSessions
from data_sync_etl.domain.search import SearchError


def setup_session(**kwargs):
    revision = ['verified-fixture-revision-1']
    clock = [0.]
    cache = SearchResultSessions(lambda _: revision[0], clock=lambda: clock[0], **kwargs)
    hits = [RankedHit(f'r{i}', f'v{i}', f'b{i}', f'u{i}', 1. / (i + 1)) for i in range(22)]
    calls = {'rank': 0, 'hydrate': 0}
    def rank():
        calls['rank'] += 1
        return hits, False
    def hydrate(rows):
        calls['hydrate'] += 1
        return [{'resource_id': row.resource_id, 'resource_version_id': row.version_id,
                 'snippet': 'fresh fixture evidence'} for row in rows]
    return cache, revision, clock, calls, rank, hydrate


def test_pages_reuse_heavy_rank_once_and_hydrate_each_page():
    cache, _, _, calls, rank, hydrate = setup_session()
    token = cache.begin('alice', 'canonical-query-fingerprint', rank)
    for page in (1, 2, 1, 3, 4):
        result = cache.page(token, 'alice', 'canonical-query-fingerprint', page, 5, hydrate)
        assert result['page'] == page and len(result['items']) == 5 and result['total'] == 22
    assert cache.begin('alice', 'canonical-query-fingerprint', rank) == token
    assert calls == {'rank': 1, 'hydrate': 5}
    assert not any(hasattr(entry, 'snippet') for entry in cache._entries.values())


@pytest.mark.parametrize('change', ['revision', 'ttl', 'logout'])
def test_revision_expiry_logout_invalidates_without_stale_snippet(change):
    cache, revision, clock, _, rank, hydrate = setup_session()
    token = cache.begin('alice', 'query', rank)
    if change == 'revision':
        revision[0] = 'revision-2'
    elif change == 'ttl':
        clock[0] = 31.
    else:
        cache.invalidate_owner('alice')
    with pytest.raises(SearchError, match='SEARCH_SESSION_MISS'):
        cache.page(token, 'alice', 'query', 1, 5, hydrate)


def test_cross_user_and_cross_query_replay_share_same_miss():
    cache, _, _, _, rank, hydrate = setup_session()
    token = cache.begin('alice', 'query', rank)
    for owner, query in [('bob', 'query'), ('alice', 'changed-query')]:
        with pytest.raises(SearchError, match='SEARCH_SESSION_MISS'):
            cache.page(token, owner, query, 1, 5, hydrate)
    assert cache.page(token, 'alice', 'query', 1, 5, hydrate)['items']


def test_during_hydration_revoke_or_denial_never_returns_old_total():
    cache, revision, _, _, rank, hydrate = setup_session()
    token = cache.begin('alice', 'query', rank)
    def revoked(rows):
        result = hydrate(rows)
        revision[0] = 'revoked'
        return result
    with pytest.raises(SearchError, match='SEARCH_SESSION_MISS'):
        cache.page(token, 'alice', 'query', 1, 5, revoked)
    token = cache.begin('alice', 'query', rank)
    with pytest.raises(SearchError, match='SEARCH_SESSION_MISS'):
        cache.page(token, 'alice', 'query', 1, 5, lambda _: [])


def test_partial_or_truncated_or_unverified_results_are_not_cached():
    cache, revision, _, _, rank, _ = setup_session()
    with pytest.raises(SearchError, match='TRUNCATED'):
        cache.begin('alice', 'query', lambda: (rank()[0], True))
    assert not cache._entries
    revision[0] = ''
    with pytest.raises(SearchError, match='UNAVAILABLE'):
        cache.begin('alice', 'query', rank)


def test_lru_per_user_and_byte_budgets_bound_retention():
    cache, _, _, _, rank, hydrate = setup_session(max_entries=2, per_owner=1)
    first = cache.begin('alice', 'q1', rank)
    cache.begin('alice', 'q2', rank)
    cache.begin('bob', 'q1', rank)
    assert len(cache._entries) == 2 and cache._bytes <= cache.max_bytes
    with pytest.raises(SearchError, match='MISS'):
        cache.page(first, 'alice', 'q1', 1, 5, hydrate)


def test_concurrent_same_request_has_one_rank_call():
    cache, _, _, calls, rank, _ = setup_session()
    entered, release = Event(), Event()
    def blocked():
        entered.set()
        assert release.wait(2.)
        return rank()
    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(cache.begin, 'alice', 'query', blocked)
        assert entered.wait(2.)
        two = pool.submit(cache.begin, 'alice', 'query', blocked)
        release.set()
        assert one.result() == two.result()
    assert calls['rank'] == 1
