"""Structured/Fallback membership is pure, disjoint and never changes PG units."""

from copy import deepcopy

import pytest
from conftest import completed

from data_sync_etl.application.content_bags import bag_structure, representation_mode
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.mongo_documents import FALLBACK_BAG_TYPES
from tests.indexing_support import seed_structure, setup_index


def unit(key, kind, parent=None, text='Học liệu toán học'):
    return dict(id=key, resource_version_id='v', unit_type=kind, parent_unit_id=parent,
                sequence_no=1, text=text)


@pytest.mark.parametrize('kind', FALLBACK_BAG_TYPES)
def test_flat_leaf_root_is_own_real_id(kind):
    rows = [unit('leaf', kind)]
    before = deepcopy(rows)
    assert bag_structure(rows) == {'leaf': ['leaf']}
    assert representation_mode(rows, bag_structure(rows)) == 'fallback'
    assert rows == before


def test_local_priority_mixed_full_disjoint_coverage_and_stable_order():
    rows = [unit('c', 'CHAPTER'), unit('l', 'LESSON', 'c'), unit('a', 'PAGE_TEXT', 'l'),
            unit('independent', 'LESSON'), unit('b', 'TXT_TEXT', 'independent'), unit('flat', 'CSV_TEXT')]
    expected = {'c': ['a'], 'flat': ['flat'], 'independent': ['b']}
    assert bag_structure(rows) == expected
    assert bag_structure(list(reversed(rows))) == expected
    assert representation_mode(rows, expected) == 'mixed'
    assert len({leaf for leaves in expected.values() for leaf in leaves}) == 3


@pytest.mark.parametrize('error', ['orphan', 'cycle', 'cross', 'duplicate'])
def test_invalid_tree_is_not_hidden_by_fallback(error):
    rows = [unit('a', 'TXT_TEXT'), unit('b', 'TXT_TEXT', 'a')]
    if error == 'orphan':
        rows[0]['parent_unit_id'] = 'missing'
    elif error == 'cycle':
        rows[0]['parent_unit_id'] = 'b'
    elif error == 'cross':
        rows[1]['resource_version_id'] = 'other'
    else:
        rows.append(rows[0])
    with pytest.raises(DomainError):
        bag_structure(rows)


def test_empty_unknown_leaves_do_not_make_bags():
    assert bag_structure([unit('a', 'TXT_TEXT', text='  '), unit('b', 'MP3_TEXT')]) == {}


def test_enum_dto_and_mongo_contract_remain_one_allow_list():
    from typing import get_args

    from data_sync_etl.adapters.mongo.schema import contracts
    from data_sync_etl.domain.mongo_documents import STRUCTURED_BAG_TYPES, BagUnitType
    allowed = {*STRUCTURED_BAG_TYPES, *FALLBACK_BAG_TYPES}
    assert set(get_args(BagUnitType)) == allowed
    assert set(contracts(384)['content_bags']['$jsonSchema']['properties']['unit_type']['enum']) == allowed
    assert len(contracts(384)) == 5


def test_mixed_worker_and_mode_transition_preserve_old_bags(container):
    index = setup_index(container)
    version, job = completed(container)
    index.enqueue(version['id'])
    index.once()
    assert index.status(version['id'])['bag_count'] == 3
    with container.uow() as repo:
        before = repo.find('content_units', {'resource_version_id': version['id']})
        generation = len(repo.find('etl_stage_runs', {'resource_version_id': version['id'], 'stage': 'MONGO_PROJECTION'}))
    seed_structure(container, version['id'])  # Explicit simulated structured upstream in isolated unit test.
    with container.uow() as repo:
        repo.update('content_units', before[0]['id'], {'parent_unit_id': None})
    container.mongo_projector.project(version['id'], job['id'], generation=generation)
    assert index.status(version['id'])['index_status'] == 'STALE'
    assert index.status(version['id'])['representation_mode'] == 'mixed'
    index.enqueue(version['id'], force=True)
    index.once()
    state = index.status(version['id'])
    assert state['index_status'] == 'READY' and state['bag_count'] == 2
    assert len(index.bags.rows) == 4  # no historic document deletion
    assert sum(doc.is_current for doc in index.bags.rows.values()) == 2


def test_flat_unit_many_previews_has_one_bag(container):
    index = setup_index(container)
    version, _ = completed(container, ['word text'])
    with container.uow() as repo:
        row = repo.find('content_units', {'resource_version_id': version['id']})[0]
        repo.update('content_unit_texts', row['id'], {'text': 'word ' * 900})
        container.documents.units[row['id']]['text'] = 'word ' * 900
    index.enqueue(version['id'])
    index.once()
    state = index.status(version['id'])
    assert state['chunk_preview_count'] > 1 and state['bag_count'] == 1
