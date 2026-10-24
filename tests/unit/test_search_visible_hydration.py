import pytest

from data_sync_etl.domain.search import SearchError
from tests.unit.test_search import searchable as searchable


def second_resource(c):
    version = c.ingestion.run('resource-2', 'input', 'sample.pdf')['version']
    c.ocr.create_job(version['id'])
    while c.worker.once('visible-search-test'):
        pass
    c.indexing.enqueue(version['id'])
    assert c.indexing.once()
    return version


def test_only_visible_winner_hydrates_snippet_evidence_without_truncating_total(searchable, monkeypatch):
    c, _ = searchable
    second_resource(c)
    original = c.search._lexical_evidence
    calls = []
    def evidence(*args):
        calls.append(args[1].id)
        return original(*args)
    monkeypatch.setattr(c.search, '_lexical_evidence', evidence)
    first = c.search.search('page text', 'hybrid', size=1)
    assert first['total'] == 2 and len(calls) == 1
    calls.clear()
    second = c.search.search('page text', 'hybrid', size=1, page=2)
    assert second['total'] == 2 and len(calls) == 1
    assert first['items'][0]['resource_id'] != second['items'][0]['resource_id']
    calls.clear()
    assert not c.search.search('page text', 'hybrid', size=1, page=3)['items']
    assert not calls


def test_visible_hydration_still_precedes_final_metadata_revalidation(searchable, monkeypatch):
    c, version = searchable
    original = c.search._lexical_evidence
    def mutate(*args):
        with c.uow() as repo:
            repo.update('master_learning_resources', version['resource_id'], {'publication_status': 'DRAFT'})
        return original(*args)
    monkeypatch.setattr(c.search, '_lexical_evidence', mutate)
    with pytest.raises(SearchError, match='SEARCH_INDEX_STALE'):
        c.search.search('page text', 'hybrid')
