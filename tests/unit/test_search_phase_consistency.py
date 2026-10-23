from copy import deepcopy

import pytest

from data_sync_etl.domain.search import SearchError
from tests.unit.test_search import searchable as searchable


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
