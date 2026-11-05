import logging
from types import SimpleNamespace

from data_sync_etl.adapters.mongo.foundation_store import MongoBagStore
from data_sync_etl.api.search_schemas import search_error


def test_unknown_search_failure_is_diagnosable_without_private_exception(caplog):
    private = 'private-dsn-and-document-text'
    with caplog.at_level(logging.INFO, logger='etl'):
        response = search_error(RuntimeError(private))
    assert response.status_code == 503
    assert response.detail['code'] == 'SEARCH_UNAVAILABLE'
    assert 'RuntimeError' in caplog.text
    assert 'UNEXPECTED_FAILURE' in caplog.text
    assert private not in caplog.text
    assert private not in str(response.detail)


def test_mongo_failure_reports_static_adapter_phase_without_query_or_connection(caplog):
    private = 'private-query-and-connection'

    class Unavailable:
        def find(self, *_args, **_kwargs):
            raise RuntimeError(private)

    store = object.__new__(MongoBagStore)
    store.database = SimpleNamespace(content_bags=Unavailable())
    with caplog.at_level(logging.INFO, logger='etl'):
        try:
            store.search_candidates(['private-version'], model='private-model', limit=10)
        except RuntimeError as exc:
            response = search_error(exc)
    assert response.status_code == 503
    assert 'foundation_store.search_candidates' in caplog.text
    for value in (private, 'private-version', 'private-model'):
        assert value not in caplog.text
        assert value not in str(response.detail)
