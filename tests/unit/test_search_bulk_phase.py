import hashlib
import json
from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.search import SearchError
from tests.unit.test_search_t2_batch import bag_data


def cursor(rows):
    value = MagicMock()
    value.sort.return_value = value
    value.limit.return_value = value
    value.max_time_ms.return_value = value
    value.__iter__.side_effect = lambda: iter(deepcopy(rows))
    return value


def store_with_data():
    bags, units = [], []
    for index in range(2):
        version = f'v-{index}'
        unit = {'_id': f'leaf-{index}', 'text': 'independent source text',
                'keywords': ['source'], 'projection_generation': 1}
        bag = bag_data()
        bag.update(_id=f'root-{index}', resource_version_id=version,
            content_fingerprint=hashlib.sha256(json.dumps([unit], ensure_ascii=False,
                sort_keys=True, separators=(',', ':')).encode()).hexdigest())
        bags.append(bag)
        units.append({**unit, 'resource_version_id': version})
    db = MagicMock()
    db.with_options.return_value = db
    db.content_bags.find.side_effect = lambda *_args, **_kwargs: cursor(bags)
    db.mongo_content_units.find.side_effect = lambda *_args, **_kwargs: cursor(units)
    return MongoBagStore(db, len(bags[0]['embedding'])), db, bags, units


def test_bulk_phase_has_two_reads_and_independent_final_phase_detects_writer():
    store, db, bags, _ = store_with_data()
    initial = store.search_phase(['v-0', 'v-1'], limit=10)
    for index in range(2):
        assert initial([f'root-{index}'], version_id=f'v-{index}', generation=1)[f'root-{index}']
    assert db.content_bags.find.call_count == db.mongo_content_units.find.call_count == 1
    bags[0]['embedding'] = [0.] * len(bags[0]['embedding'])
    with pytest.raises(DomainError):
        store.search_phase(['v-0', 'v-1'], limit=10)
    assert db.content_bags.find.call_count == 2


def test_bulk_phase_never_crosses_version_or_generation_and_rechecks_text():
    store, _, _, units = store_with_data()
    read = store.search_phase(['v-0', 'v-1'], limit=10)
    assert read(['root-1'], version_id='v-0', generation=1)['root-1'] is None
    assert read(['root-0'], version_id='v-0', generation=2)['root-0'] is None
    units[0]['text'] = 'writer changed source without updating the bag'
    final = store.search_phase(['v-0', 'v-1'], limit=10)
    assert final(['root-0'], version_id='v-0', generation=1)['root-0'] is None


def test_bulk_phase_rejects_unknown_bag_fields_instead_of_projecting_them_out():
    store, _, bags, _ = store_with_data()
    bags[0]['license_restricted'] = True
    with pytest.raises(DomainError):
        store.search_phase(['v-0', 'v-1'], limit=10)


def test_bulk_phase_enforces_content_budget_without_truncating_results():
    store, _, _, _ = store_with_data()
    with pytest.raises(SearchError, match='SEARCH_LIMIT_EXCEEDED'):
        store.search_phase(['v-0', 'v-1'], limit=10, max_chars=1)
