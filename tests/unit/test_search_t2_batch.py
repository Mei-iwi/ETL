"""Search-only Mongo batching invariants; fixtures, not production data."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore
from data_sync_etl.application.content_bags import ContentBagBuilder
from data_sync_etl.domain.core import DomainError
from tests.search_read_path_probe import ACTIVE, Probe, Trace
from tests.unit.test_mongo_foundation import StubEmbedding, leaf, tree
from tests.unit.test_search import searchable as searchable


def bag_data():
    return ContentBagBuilder(StubEmbedding()).build(
        'v-1', tree(), [leaf()], generation=1, content_fingerprint='a' * 64).bags[0].bson()


def adapter(rows):
    database = MagicMock()
    database.with_options.return_value = database
    store = MongoBagStore(database, StubEmbedding.dimension)
    cursor = database.content_bags.find.return_value
    cursor.sort.return_value = cursor
    cursor.limit.return_value = cursor
    cursor.__iter__.side_effect = lambda: iter(deepcopy(rows))
    store.reader.content_fingerprint = MagicMock(return_value='a' * 64)
    return store, database


def test_batch_reads_bags_before_fresh_fingerprint_and_does_not_write():
    raw = bag_data()
    store, db = adapter([raw])
    observed = []
    cursor = db.content_bags.find.return_value
    cursor.__iter__.side_effect = lambda: (observed.append('bags') or iter([raw]))
    def fp(*args, **kwargs):
        observed.append('fingerprint')
        assert args == ('v-1',) and kwargs == {'generation': 1}
        return 'a' * 64
    store.reader.content_fingerprint.side_effect = fp
    result = store.search_bags([raw['_id']], version_id='v-1', generation=1)
    assert result[raw['_id']].bson() == raw and observed == ['bags', 'fingerprint']
    (selector,) = db.content_bags.find.call_args.args
    assert selector == {'_id': {'$in': [raw['_id']]}, 'resource_version_id': 'v-1', 'is_current': True}
    # No projection may mask unknown/restriction fields before strict validation.
    db.content_bags.find.return_value.limit.assert_called_once_with(2)
    db.content_bags.update_one.assert_not_called()
    db.content_bags.delete_many.assert_not_called()
    # No cross-request reuse, including the independent post-read fingerprint.
    store.search_bags([raw['_id']], version_id='v-1', generation=1)
    assert store.reader.content_fingerprint.call_count == 2


@pytest.mark.parametrize('change', ['missing', 'inactive', 'generation', 'fingerprint', 'dimension', 'no_content'])
def test_missing_or_stale_bag_never_becomes_valid(change):
    raw = bag_data()
    changes = {'inactive': {'is_current': False}, 'generation': {'projection_generation': 2},
               'fingerprint': {'content_fingerprint': 'b' * 64}, 'dimension': {'embedding': [.1, .2, .3]}}
    raw.update(changes.get(change, {}))
    store, _ = adapter([] if change == 'missing' else [raw])
    if change == 'no_content':
        store.reader.content_fingerprint.side_effect = DomainError('fixture changed')
    assert store.search_bags([raw['_id']], version_id='v-1', generation=1) == {raw['_id']: None}


@pytest.mark.parametrize('change', ['duplicate', 'extra', 'wrong_version'])
def test_duplicate_extra_wrong_version_is_rejected(change):
    raw = bag_data()
    rows = [raw, raw] if change == 'duplicate' else [{**raw, '_id': 'other'}] if change == 'extra' else [
        {**raw, 'resource_version_id': 'wrong-version'}]
    store, _ = adapter(rows)
    with pytest.raises(DomainError, match='identity mismatch'):
        store.search_bags([raw['_id']], version_id='v-1', generation=1)


def test_chunk500_and_bounds_no_cross_phase_cache():
    store, db = adapter([])
    assert store.search_bags([], version_id='v-1', generation=1) == {}
    db.content_bags.find.assert_not_called()
    ids = [f'root-{i}' for i in range(1001)]
    assert all(v is None for v in store.search_bags(ids, version_id='v-1', generation=1).values())
    assert [len(c.args[0]['_id']['$in']) for c in db.content_bags.find.call_args_list] == [500, 500, 1]
    for bad in (['x', 'x'], ['x' * 31], [str(i) for i in range(10001)]):
        with pytest.raises(DomainError):
            store.search_bags(bad, version_id='v-1', generation=1)
    assert db.content_bags.find.call_count == 3


@pytest.mark.parametrize('invalid', ['acl', 'legacy', 'missing', 'type'])
def test_invalid_full_document_is_not_projected_away(invalid):
    raw = bag_data()
    if invalid == 'acl':
        raw['acl_denied'] = True
    elif invalid == 'legacy':
        raw['legacy_content'] = 'untrusted'
    elif invalid == 'missing':
        del raw['embedding']
    else:
        raw['keywords'] = {'unexpected': 'object'}
    store, db = adapter([raw])
    db.content_bags.find_one.return_value = raw
    with pytest.raises(DomainError, match='Invalid Mongo document'):
        store.get_bag(raw['_id'])
    with pytest.raises(DomainError, match='Invalid Mongo document'):
        store.search_bags([raw['_id']], version_id='v-1', generation=1)


def test_non_http_readers_keep_original_point_verification(searchable, monkeypatch):
    c, version = searchable
    def forbidden(*_args, **_kwargs):
        raise AssertionError('Search HTTP batch must not affect shared callers')
    monkeypatch.setattr(c.indexing.bags, 'search_bags', forbidden, raising=False)
    for method in ('bow', 'vector', 'hybrid'):
        assert c.search.search('page', method)['total'] == 1
    with c.uow() as repo:
        snapshot = c.indexing._snapshot(repo, version['id'])
        run = c.indexing._runs(repo, version['id'])[0]
        assert c.indexing._verified(snapshot, run)


def test_profiler_request_id_collision_cannot_lose_commands():
    probe = Probe()
    traces = [Trace('request-' + str(i), True) for i in range(2)]
    events = [SimpleNamespace(connection_id=('127.0.0.1', 12345), request_id=7,
        server_connection_id=i, command_name='find', command={'find': 'content_bags'},
        reply={'cursor': {'firstBatch': []}}, duration_micros=100) for i in (1, 2)]
    for trace, event in zip(traces, events, strict=True):
        token = ACTIVE.set(trace)
        try:
            probe.mongo.started(event)
        finally:
            ACTIVE.reset(token)
    assert len(probe.mongo.pending) == 2
    for event in reversed(events):
        probe.mongo.succeeded(event)
    assert not probe.mongo.pending
    assert [len(trace.operations) for trace in traces] == [1, 1]
