"""Counterexamples for reusing the existing PG-only marker as a Search cache guard.

These are isolated fixture mutations, not changes to Dev data or write paths.
Passing means the gap is reproduced, NOT that a pagination cache is safe.
"""
import pytest

from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('mutation', ['bag_embedding', 'bag_current', 'mongo_text', 'pg_text'])
def test_pg_marker_is_not_a_cross_store_search_epoch(searchable, mutation):
    c, version = searchable

    def marker():
        with c.uow() as repo:
            return repo.evaluation_state({}, limit=100, max_units=10000)

    before = c.search.search('page text', 'vector')
    assert before['items']
    observed = marker()
    bag = next(iter(c.indexing.bags.rows.values()))
    if mutation == 'bag_embedding':
        bag.embedding = [0.3, 0.2, 0.1]
    elif mutation == 'bag_current':
        bag.is_current = False
    elif mutation == 'mongo_text':
        next(iter(c.documents.units.values()))['text'] += ' fixture edit'
    else:
        with c.uow() as repo:
            unit = repo.find('content_units', {'resource_version_id': version['id']})[0]
            repo.update('content_unit_texts', unit['id'], {'text': 'Changed isolated fixture text'})
    assert marker() == observed
    after = c.search.search('page text', 'vector')
    assert before['items'] != after['items']
