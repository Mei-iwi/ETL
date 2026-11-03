from copy import deepcopy

import pytest

from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.indexing import IndexingError
from tests.unit.test_search import searchable as searchable


def test_bulk_verification_uses_phase_fingerprint_and_rejects_a_changed_final_phase(searchable, monkeypatch):
    c, version = searchable
    with c.uow() as repo:
        snapshot = c.indexing._snapshot(repo, version['id'])
        run = c.indexing._runs(repo, version['id'])[0]
    fingerprint = c.indexing.reader.content_fingerprint(version['id'], generation=snapshot[1])

    def phase(ids, **_kwargs):
        return {key: deepcopy(c.indexing.bags.rows.get(key)) for key in ids}

    phase.content_phase = True
    phase.content_fingerprint = lambda *_a, **_k: fingerprint
    monkeypatch.setattr(c.indexing.reader, 'content_fingerprint',
                        lambda *_a, **_k: pytest.fail('bulk phase repeated a per-version Mongo read'))
    assert c.indexing._verified(snapshot, run, bag_reader=phase)
    phase.content_fingerprint = lambda *_a, **_k: 'f' * 64
    with pytest.raises(IndexingError, match='INDEX_ARTIFACT_INVALID'):
        c.indexing._verified(snapshot, run, bag_reader=phase)


def test_http_style_phases_are_independent_and_final_bag_mutation_is_rejected(searchable, monkeypatch):
    container, _ = searchable
    calls = []
    def phase(version_ids, *, limit, max_chars):
        calls.append(tuple(version_ids))
        if len(calls) == 2:
            next(iter(container.indexing.bags.rows.values())).embedding[0] += .123
        observed = deepcopy(container.indexing.bags.rows)
        def read(ids, *, version_id, generation):
            return {key: observed.get(key) for key in ids}
        return read
    monkeypatch.setattr(container.indexing.bags, 'search_phase', phase, raising=False)
    with pytest.raises(SearchError, match='SEARCH_INDEX_STALE'):
        container.search.search('page', 'bow', _batch_texts=True, _batch_bags=True)
    assert len(calls) == 2


def test_every_search_gets_new_phases_and_permission_is_rechecked(searchable, monkeypatch):
    container, version = searchable
    calls = []
    def phase(version_ids, *, limit, max_chars):
        calls.append(tuple(version_ids))
        observed = deepcopy(container.indexing.bags.rows)
        return lambda ids, **kwargs: {key: observed.get(key) for key in ids}
    monkeypatch.setattr(container.indexing.bags, 'search_phase', phase, raising=False)
    result = container.search.search('page', 'bow', _batch_texts=True, _batch_bags=True)
    assert result['total'] == 1 and len(calls) == 2
    with container.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'is_active': False})
    assert not container.search.search('page', 'bow', _batch_texts=True, _batch_bags=True)['items']


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_candidate_reuse_preserves_results_and_rejects_final_mutation(searchable, monkeypatch, method):
    container, _ = searchable
    bags = container.indexing.bags
    baseline = container.search.search('page', method, _batch_texts=True, _batch_bags=True)
    calls = []
    mutate = False

    def initial(self, version_ids, candidates, **options):
        calls.append('initial')
        observed = {bag.id: deepcopy(bag) for bag in candidates}
        return lambda ids, **kwargs: {key: observed.get(key) for key in ids}

    def final(version_ids, **options):
        calls.append('final')
        observed = deepcopy(bags.rows)
        if mutate:
            next(iter(observed.values())).embedding[0] += .123
        return lambda ids, **kwargs: {key: observed.get(key) for key in ids}

    monkeypatch.setattr(type(bags), 'search_phase_from_candidates', initial, raising=False)
    monkeypatch.setattr(bags, 'search_phase', final, raising=False)
    result = container.search.search('page', method, _batch_texts=True, _batch_bags=True)
    assert result['items'] == baseline['items']
    assert result['total'] == baseline['total']
    assert calls == ['initial', 'final']
    calls.clear()
    mutate = True
    with pytest.raises(SearchError, match='SEARCH_INDEX_STALE'):
        container.search.search('page', method, _batch_texts=True, _batch_bags=True)
    assert calls == ['initial', 'final']
