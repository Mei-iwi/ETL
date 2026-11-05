import pytest

from data_sync_etl.application.graph_sync import graph_diff, graph_lock
from data_sync_etl.domain.graph_contract import build_snapshot
from tests.unit.test_graph_snapshot import graph_tables


def test_endpoint_handoff_requires_old_receipt_same_database_and_exact_graph(tmp_path):
    import json
    from types import SimpleNamespace

    from data_sync_etl.application.graph_sync import GraphSynchronizer
    from data_sync_etl.domain.graph_contract import GraphContractError, GraphSnapshot

    snapshot = build_snapshot(graph_tables())

    def projection(endpoint, database='verified-database', value=snapshot):
        return SimpleNamespace(driver=SimpleNamespace(settings=SimpleNamespace(
            neo4j_runtime='host', neo4j_uri_host=endpoint, neo4j_database='neo4j')),
            database_identity=lambda: database, read=lambda: value, verify_schema=lambda: None)

    source = projection('bolt://127.0.0.1:7687')
    target = projection('bolt://neo4j:7687')
    sync = GraphSynchronizer(None, source, tmp_path)
    with pytest.raises(GraphContractError, match='GRAPH_OWNERSHIP_UNPROVEN'):
        sync.authorize_same_database_endpoint(target)
    original = json.dumps(sync._identity())
    sync._receipt(sync._identity()).write_text(original, encoding='utf-8')
    with pytest.raises(GraphContractError, match='GRAPH_ENDPOINT_DATABASE_MISMATCH'):
        sync.authorize_same_database_endpoint(projection('bolt://neo4j:7687', 'other-database'))
    with pytest.raises(GraphContractError, match='GRAPH_ENDPOINT_SNAPSHOT_MISMATCH'):
        sync.authorize_same_database_endpoint(projection('bolt://neo4j:7687', value=GraphSnapshot(frozenset(), frozenset())))
    assert sync.authorize_same_database_endpoint(target)['graph_writes'] == 0
    assert sync._receipt(sync._identity()).read_text(encoding='utf-8') == original
    target_sync = GraphSynchronizer(None, target, tmp_path)
    target_sync._verify_owner(target_sync._identity())
    # A different arbitrary endpoint still fails even with the same database ID.
    unknown = GraphSynchronizer(None, projection('bolt://unknown:7687'), tmp_path)
    with pytest.raises(GraphContractError, match='GRAPH_OWNERSHIP_UNPROVEN'):
        unknown._verify_owner(unknown._identity())


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
