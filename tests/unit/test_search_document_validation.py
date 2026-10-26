from copy import deepcopy

import pytest

from data_sync_etl.adapters.mongo.foundation_store import checked
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.mongo_documents import ContentBagDocument
from data_sync_etl.ports.search import SEARCH_DOCUMENT_VALIDATION, document_validation_scope
from tests.unit.test_search import searchable as searchable
from tests.unit.test_search_t2_batch import adapter, bag_data


def test_validation_reuses_full_bson_only_and_copies_mutable_results(monkeypatch):
    raw, calls = bag_data(), []
    validate = ContentBagDocument.model_validate
    def counted(value):
        calls.append(1)
        return validate(value)
    monkeypatch.setattr(ContentBagDocument, 'model_validate', counted)
    with document_validation_scope():
        first = checked(ContentBagDocument, raw)
        first.embedding[0] = 123.0
        assert checked(ContentBagDocument, deepcopy(raw)).embedding == raw['embedding']
        assert len(calls) == 1
        for change in ({'restricted': True}, {'embedding': [float('nan')]}):
            with pytest.raises(DomainError):
                checked(ContentBagDocument, {**raw, **change})
        assert len(calls) == 3
    assert SEARCH_DOCUMENT_VALIDATION.get() is None
    with document_validation_scope():
        checked(ContentBagDocument, raw)
    assert len(calls) == 4


def test_validation_never_reuses_a_stale_database_read():
    raw = bag_data()
    store, database = adapter([raw])
    with document_validation_scope():
        assert store.search_bags([raw['_id']], version_id='v-1', generation=1)[raw['_id']]
        raw['is_current'] = False
        assert store.search_bags([raw['_id']], version_id='v-1', generation=1)[raw['_id']] is None
    assert database.content_bags.find.call_count == 2
    assert store.reader.content_fingerprint.call_count == 2


@pytest.mark.parametrize('searchable', [{'meanings': True}], indirect=True)
def test_nested_meaning_vectors_are_copied_and_tampering_is_revalidated(searchable):
    container, _ = searchable
    raw = next(iter(container.indexing.bags.rows.values())).bson()
    with document_validation_scope():
        first = checked(ContentBagDocument, raw)
        first.meanings.members[0].embedding[0] = 123.0
        second = checked(ContentBagDocument, raw)
        assert second.meanings.members[0].embedding == raw['meanings']['members'][0]['embedding']
        changed = deepcopy(raw)
        changed['meanings']['members'][0]['embedding'][0] = 123.0
        with pytest.raises(DomainError):
            checked(ContentBagDocument, changed)


def test_validation_retention_is_bounded_and_exception_releases_scope():
    raw = bag_data()
    with pytest.raises(RuntimeError), document_validation_scope():
        state = SEARCH_DOCUMENT_VALIDATION.get()
        state['bytes'] = 16 * 1024 * 1024
        checked(ContentBagDocument, raw)
        assert not state['documents']
        raise RuntimeError('cancelled')
    assert SEARCH_DOCUMENT_VALIDATION.get() is None
