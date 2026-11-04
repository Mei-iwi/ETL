import pytest

from data_sync_etl.application.graph_sync import graph_diff, graph_lock
from data_sync_etl.domain.graph_contract import build_snapshot
from tests.unit.test_graph_snapshot import graph_tables


def test_diff_compares_sets_not_counts():
    t = graph_tables()
    old = build_snapshot(t)
    t['content_units'][1]['id'] = 'changed-leaf'
    new = build_snapshot(t)
    diff = graph_diff(new, old)
    assert len(diff['missing_nodes']) == len(diff['extra_nodes']) == 1
    assert len(diff['missing_edges']) == len(diff['extra_edges']) == 2
    assert old.counts() == new.counts()
    assert any(diff.values())


def test_os_lock_rejects_concurrent_writer_and_recovers(tmp_path):
    from data_sync_etl.domain.graph_contract import GraphContractError
    path = tmp_path / 'sync.lock'
    with graph_lock(path):
        with pytest.raises(GraphContractError, match='GRAPH_SYNC_BUSY'), graph_lock(path):
            pytest.fail('Second writer must not enter')
    with graph_lock(path):
        pass
