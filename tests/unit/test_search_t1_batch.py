"""HTTP-only T1 opt-in and proof that shared/non-Search point reads are unchanged."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event
from sqlalchemy.exc import IntegrityError

from data_sync_etl.db.models import ContentUnitText
from data_sync_etl.domain.indexing import IndexingError
from data_sync_etl.main import create_app
from tests.unit.test_search import searchable as searchable


@pytest.mark.parametrize('searchable', [False, True], indirect=True)
def test_http_batch_matches_baseline_and_does_not_change_other_callers(searchable, monkeypatch):
    c, version = searchable
    with c.uow() as repo:
        before = c.indexing._snapshot(repo, version['id'])
        after = c.indexing._snapshot(repo, version['id'], text_reader=repo.search_unit_texts)
        assert before == after
    def forbidden(*_args, **_kwargs):
        raise AssertionError('T1 must not activate for direct Search or shared non-Search calls')
    from data_sync_etl.adapters.persistence.search_reader import SearchRepositoryMixin
    original_reader = SearchRepositoryMixin.search_unit_texts
    monkeypatch.setattr(SearchRepositoryMixin, 'search_unit_texts', forbidden)
    with c.uow() as repo:
        assert c.indexing._snapshot(repo, version['id']) == before
    for method in ('bow', 'vector', 'hybrid'):
        assert c.search.search('page', method)['total'] == 1  # Direct caller/Evaluation boundary.
    calls = []
    def counted(repo, ids):
        calls.append(tuple(ids))
        return original_reader(repo, ids)
    monkeypatch.setattr(SearchRepositoryMixin, 'search_unit_texts', counted)
    with TestClient(create_app(c), base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as api:
        for method in ('bow', 'vector', 'hybrid'):
            response = api.post('/api/v1/search', json={'query': 'page', 'method': method})
            assert response.status_code == 200 and response.json()['total'] == 1
        assert api.post('/api/v1/search', json={'query': 'page', '_batch_texts': True}).status_code == 422
    assert len(calls) == 6  # Separate initial/final reads for each of three methods.


def test_reader_bounded_500_ids_and_empty_input(searchable):
    c, _version = searchable
    sizes = []
    def before(_conn, _cursor, statement, parameters, _ctx, _many):
        if statement.lstrip().upper().startswith('SELECT') and 'FROM content_unit_texts' in statement:
            sizes.append(len(parameters))
    event.listen(c.engine, 'before_cursor_execute', before)
    try:
        with c.uow() as repo:
            assert repo.search_unit_texts([]) == {}
            assert not sizes
            assert repo.search_unit_texts([f'missing-{index}' for index in range(1001)]) == {}
            assert sizes == [500, 500, 1]
            for ids in (['same', 'same'], ['x' * 31], [str(i) for i in range(10001)]):
                with pytest.raises(IndexingError, match='CONTENT_NOT_READY'):
                    repo.search_unit_texts(ids)
            assert sizes == [500, 500, 1]
    finally:
        event.remove(c.engine, 'before_cursor_execute', before)


def test_missing_row_null_and_duplicate_source_invariants(searchable):
    c, version = searchable
    with c.uow() as repo:
        units = repo.find('content_units', {'resource_version_id': version['id']})
        original = repo.get('content_unit_texts', units[0]['id'])
    # NULL and duplicate text rows are forbidden by the actual schema, not valid
    # fixtures to silently normalize. Transactions below are owned SQLite fixtures.
    with pytest.raises(IntegrityError), c.uow() as repo:
        repo.update('content_unit_texts', units[0]['id'], {'text': None})
    with pytest.raises(IntegrityError), c.uow() as repo:
        repo.insert('content_unit_texts', original)
    with c.uow() as repo:
        repo.session.execute(delete(ContentUnitText).where(ContentUnitText.content_unit_id == units[0]['id']))
    for enabled in (False, True):
        with c.uow() as repo:
            with pytest.raises(IndexingError, match='CONTENT_NOT_READY'):
                c.indexing._snapshot(repo, version['id'], **({'text_reader': repo.search_unit_texts} if enabled else {}))
