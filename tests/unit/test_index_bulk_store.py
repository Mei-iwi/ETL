from unittest.mock import MagicMock

import pytest
from pymongo.errors import BulkWriteError

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore
from data_sync_etl.domain.core import DomainError, now
from data_sync_etl.domain.mongo_documents import ContentBagDocument


def bag(key='root-1', **changes):
    return ContentBagDocument(_id=key, resource_version_id='v1', created_at=now(), updated_at=now(),
        projection_generation=2, unit_type='PAGE_TEXT', keywords=['plant'], embedding_text='plant',
        embedding=[0.5, 0.5], embedding_model='model@revision', is_current=True,
        content_fingerprint='a' * 64).model_copy(update=changes)


def store():
    database = MagicMock()
    database.with_options.return_value = database
    adapter = MongoBagStore(database, 2)
    adapter.reader.content_fingerprint = MagicMock(return_value='a' * 64)
    return adapter, database


def test_bulk_checks_content_before_and_after_and_preserves_generation_fencing():
    adapter, database = store()
    adapter.upsert_bags([bag(), bag('root-2')])
    assert adapter.reader.content_fingerprint.call_count == 2
    operations = database.content_bags.bulk_write.call_args.args[0]
    assert len(operations) == 2
    for operation in operations:
        assert operation._filter['$or'][0]['projection_generation'] == {'$lte': 2}
        assert operation._upsert is True
    assert database.content_bags.bulk_write.call_args.kwargs == {'ordered': True}


@pytest.mark.parametrize('mutation', [
    {'resource_version_id': 'other'}, {'projection_generation': 3}, {'is_current': False},
    {'content_fingerprint': 'b' * 64}, {'embedding': [0.1]},
])
def test_mixed_stale_or_invalid_batches_do_not_write(mutation):
    adapter, database = store()
    with pytest.raises(DomainError):
        adapter.upsert_bags([bag(), bag('root-2', **mutation)])
    database.content_bags.bulk_write.assert_not_called()


def test_content_mutation_after_bulk_invalidates_only_same_or_older_generation():
    adapter, database = store()
    adapter.reader.content_fingerprint.side_effect = ['a' * 64, 'b' * 64]
    with pytest.raises(DomainError, match='snapshot changed'):
        adapter.upsert_bags([bag()])
    selector = database.__getitem__.return_value.update_many.call_args.args[0]
    assert selector['resource_version_id'] == 'v1'
    assert selector['$or'][0]['projection_generation'] == {'$lte': 2}


def test_partial_bulk_failure_fails_closed_and_redacts_driver_details():
    adapter, database = store()
    database.content_bags.bulk_write.side_effect = BulkWriteError({
        'writeErrors': [{'code': 11000, 'errmsg': 'private driver details'}]})
    with pytest.raises(DomainError, match='^Finalization ownership lost$'):
        adapter.upsert_bags([bag()])
    database.__getitem__.return_value.update_many.assert_called_once()


def test_large_write_uses_bounded_network_batches_without_repeated_fingerprints():
    adapter, database = store()
    adapter.upsert_bags([bag('root-' + str(index)) for index in range(101)])
    assert [len(call.args[0]) for call in database.content_bags.bulk_write.call_args_list] == [100, 1]
    assert adapter.reader.content_fingerprint.call_count == 2


def test_duplicate_or_empty_batches_do_not_write():
    adapter, database = store()
    for documents in ([], [bag(), bag()]):
        with pytest.raises(DomainError):
            adapter.upsert_bags(documents)
    database.content_bags.bulk_write.assert_not_called()
