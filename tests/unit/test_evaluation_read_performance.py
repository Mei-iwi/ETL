import pytest

from data_sync_etl.domain.evaluation import EvaluationError
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_evaluation import api
from tests.unit.test_evaluation import evaluation as evaluation
from tests.unit.test_search import searchable as searchable


def test_corpus_hashes_the_same_verified_bag_without_refetch(evaluation, monkeypatch):
    c, _, _ = evaluation
    original = c.search.bags.get_bag
    reads = []
    def read(key):
        reads.append(key)
        return original(key)
    monkeypatch.setattr(c.search.bags, 'get_bag', read)
    snapshot, _ = c.evaluations.corpus({}, SearchAccess('local_demo'))
    assert reads and len(reads) == len(set(reads))
    assert sum(len(v['bags']) for v in snapshot['versions']) == len(reads)


def test_history_scope_filters_before_loading_unreferenced_content(evaluation, monkeypatch):
    c, _, _ = evaluation
    monkeypatch.setattr(c.search, '_ready_snapshot', lambda *a, **kw: pytest.fail('Unreferenced content read'))
    with pytest.raises(EvaluationError) as error:
        c.evaluations.corpus({}, SearchAccess('local_demo'), set())
    assert error.value.code == 'EVALUATION_NO_READY_CORPUS'


def test_isolated_evaluation_uses_fresh_batch_reads_for_warmup_and_measurement(evaluation, monkeypatch):
    c, _, command = evaluation
    original = c.search.search
    calls = []
    def search(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)
    monkeypatch.setattr(c.search, 'search', search)
    with api(c) as client:
        created = client.post('/api/v1/evaluations/datasets', json=command)
        result = client.post('/api/v1/evaluations/runs', json={
            'dataset_id': created.json()['dataset_id'], 'warmup_queries': 1})
    assert result.status_code == 201 and result.json()['status'] == 'COMPLETED'
    assert len(calls) == 9
    assert all(call['_batch_texts'] is True and call['_batch_bags'] is True for call in calls)
