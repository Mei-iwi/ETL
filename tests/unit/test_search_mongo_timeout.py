"""Transient content transport failures never publish a partial/stale corpus."""
import logging

import pytest
from pymongo.errors import NetworkTimeout

from data_sync_etl.adapters.mongo.foundation_store import _search_cursor
from data_sync_etl.api.search_schemas import search_error
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.domain.search import SearchError
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_metadata_search import search


@pytest.mark.parametrize('stage', ['find', 'get_more'])
def test_timeout_is_typed_without_returning_partial_documents_or_private_details(stage, caplog):
    private = 'private-uri-query-and-content'

    class Cursor:
        def sort(self, *_):
            return self

        limit = max_time_ms = batch_size = sort

        def __iter__(self):
            yield {'private': private}
            raise NetworkTimeout(private)

    class Collection:
        def find(self, *_):
            if stage == 'find':
                raise NetworkTimeout(private)
            return Cursor()

    with caplog.at_level(logging.INFO, logger='etl'):
        with pytest.raises(IndexingError, match='SEARCH_DATA_TIMEOUT') as failure:
            list(_search_cursor(Collection(), {'private': private}, 10))
    response = search_error(failure.value)
    assert response.status_code == 503
    assert response.headers['Retry-After'] == '15'
    assert private not in caplog.text
    assert private not in str(response.detail)


@pytest.mark.parametrize('method', ['bow', 'vector', 'hybrid'])
def test_healthy_metadata_remains_available_when_content_transport_times_out(metadata, monkeypatch, method):
    c, version, _, service = metadata
    service.reconcile(apply=True, dense=method != 'bow')
    c.settings.search_metadata_content_enabled = True

    def unavailable(*_args, **_kwargs):
        raise IndexingError('SEARCH_DATA_TIMEOUT', 503)

    monkeypatch.setattr(c.search, '_content_search', unavailable)
    result = search(c, method=method)
    assert result['total'] == 1
    assert result['items'][0]['resource_version_id'] == version['id']
    assert result['items'][0]['match_origin'] == 'METADATA'
    assert result['items'][0]['context_id'] is None
    assert result['branch_status']['content'] == 'SEARCH_DATA_TIMEOUT'
    assert result['branch_status']['metadata'] == 'READY'


def test_unavailable_metadata_and_content_do_not_become_a_successful_empty_search(metadata, monkeypatch):
    c, _, _, _ = metadata
    c.settings.search_metadata_content_enabled = True
    monkeypatch.setattr(c.search, '_content_search', lambda *_a, **_k: (_ for _ in ()).throw(
        IndexingError('SEARCH_DATA_TIMEOUT', 503)))
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c)


@pytest.mark.parametrize('live_lexical', [False, True])
def test_private_metadata_revoked_during_content_timeout_is_not_returned(metadata, monkeypatch, live_lexical):
    c, version, _, service = metadata
    service.reconcile(apply=True)
    if live_lexical:
        c.settings.search_metadata_live_lexical_fallback_enabled = True
        monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(
            IndexingError('METADATA_SCHEMA_NOT_READY')))
    c.settings.search_metadata_content_enabled = True

    def revoked(*_args, **_kwargs):
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'publication_status': 'DRAFT'})
        raise IndexingError('SEARCH_DATA_TIMEOUT', 503)

    monkeypatch.setattr(c.search, '_content_search', revoked)
    access = SearchAccess('authenticated_user', owner_id='stranger', allow_public=True,
                          allow_shared_unreviewed=True)
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c, access=access)


@pytest.mark.parametrize('method', ['bow', 'hybrid'])
def test_opt_in_live_metadata_fallback_needs_no_extra_collection_or_embedding(metadata, monkeypatch, method):
    c, version, _, service = metadata
    c.settings.search_metadata_live_lexical_fallback_enabled = True
    c.settings.search_metadata_indexed_candidates_enabled = True
    c.settings.search_metadata_content_enabled = True
    monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(
        IndexingError('METADATA_SCHEMA_NOT_READY')))

    def forbidden(*_args, **_kwargs):
        pytest.fail('Live lexical fallback touched stored index or embedding')

    for name in ('get_many', 'upsert', 'invalidate', 'page'):
        monkeypatch.setattr(service.store, name, forbidden)
    monkeypatch.setattr(service.embedding, 'embed_documents', forbidden)
    monkeypatch.setattr(service.embedding, 'embed_queries', forbidden)
    monkeypatch.setattr(c.search, '_content_search', lambda *_a, **_k: (_ for _ in ()).throw(
        IndexingError('SEARCH_DATA_TIMEOUT', 503)))
    result = search(c, method=method)
    assert result['total'] == 1
    assert result['items'][0]['resource_version_id'] == version['id']
    assert result['items'][0]['match_origin'] == 'METADATA'
    assert result['branch_status']['metadata_storage'] == 'LIVE_LEXICAL'
    assert result['branch_status']['content'] == 'SEARCH_DATA_TIMEOUT'
    assert result['branch_status']['metadata_dense'] != 'READY'
    assert not service.store.rows and not service.embedding.calls


@pytest.mark.parametrize('enabled,method', [(False, 'bow'), (True, 'vector')])
def test_live_metadata_fallback_is_opt_in_and_never_substitutes_lexical_for_vector(
        metadata, monkeypatch, enabled, method):
    c, _, _, service = metadata
    c.settings.search_metadata_live_lexical_fallback_enabled = enabled
    c.settings.search_metadata_content_enabled = True
    monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(
        IndexingError('METADATA_SCHEMA_NOT_READY')))
    monkeypatch.setattr(c.search, '_content_search', lambda *_a, **_k: (_ for _ in ()).throw(
        IndexingError('SEARCH_DATA_TIMEOUT', 503)))
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c, method=method)


def test_large_content_timeout_has_bounded_backoff_without_blocking_filtered_reads(metadata, monkeypatch):
    c, version, _, service = metadata
    c.settings.search_metadata_live_lexical_fallback_enabled = True
    c.settings.search_metadata_content_enabled = True
    monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(
        IndexingError('METADATA_SCHEMA_NOT_READY')))
    clock, calls = [0.], []
    monkeypatch.setattr('data_sync_etl.application.search.monotonic', lambda: clock[0])

    def unavailable(*_args, **_kwargs):
        calls.append(True)
        raise IndexingError('SEARCH_DATA_TIMEOUT', 503)

    monkeypatch.setattr(c.search, '_content_search', unavailable)
    assert search(c)['total'] == 1
    assert search(c)['branch_status']['content_retry'] == 'BACKOFF'
    assert len(calls) == 1
    assert search(c, filters={'resource_id': version['resource_id']})['total'] == 1
    assert len(calls) == 2
    clock[0] = 11.
    assert search(c)['total'] == 1
    assert len(calls) == 3  # Backoff expires; skipped requests never extend it.


def test_backoff_reauthorizes_metadata_instead_of_replaying_previous_results(metadata, monkeypatch):
    c, version, _, service = metadata
    c.settings.search_metadata_live_lexical_fallback_enabled = True
    c.settings.search_metadata_content_enabled = True
    # Exercise authorization during a fixed backoff window, independently of
    # first-use analyzer initialization on a busy Windows test runner.
    monkeypatch.setattr('data_sync_etl.application.search.monotonic', lambda: 0.)
    monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(
        IndexingError('METADATA_SCHEMA_NOT_READY')))
    monkeypatch.setattr(c.search, '_content_search', lambda *_a, **_k: (_ for _ in ()).throw(
        IndexingError('SEARCH_DATA_TIMEOUT', 503)))
    access = SearchAccess('authenticated_user', owner_id='stranger', allow_public=True,
                          allow_shared_unreviewed=True)
    assert search(c, access=access)['total'] == 1
    with c.uow() as repo:
        repo.update('master_learning_resources', version['resource_id'], {'publication_status': 'DRAFT'})
    with pytest.raises(SearchError, match='SEARCH_DATA_TIMEOUT'):
        search(c, access=access)


def test_opt_in_fallback_does_not_add_metadata_votes_to_healthy_content_search(metadata, monkeypatch):
    c, _, _, service = metadata
    c.settings.search_metadata_content_enabled = True
    monkeypatch.setattr(service.store, 'ensure_ready', lambda: (_ for _ in ()).throw(
        IndexingError('METADATA_SCHEMA_NOT_READY')))

    def healthy(query, method, filters, page, size, **_kwargs):
        return dict(query=query, method=method, page=page, size=size, items=[],
                    ready_bag_count=1, representation_unit='content_bag',
                    access_scope='local_demo', message='NO_MATCHES')

    monkeypatch.setattr(c.search, '_content_search', healthy)
    before = search(c)
    c.settings.search_metadata_live_lexical_fallback_enabled = True
    assert search(c) == before
    assert before['items'] == []
    assert before['branch_status']['metadata'] == 'METADATA_SCHEMA_NOT_READY'
