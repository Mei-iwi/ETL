"""Required opt-in live checks: run this path explicitly with ETL_GRAPH_TEST_ENV.

No skipping. Only the isolated test container may be used; never .env production.
"""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

from data_sync_etl.adapters.neo4j.driver import GraphDriver
from data_sync_etl.adapters.neo4j.projection import Neo4jProjection
from data_sync_etl.config import Settings
from data_sync_etl.domain.graph_contract import GraphContractError, build_snapshot
from tests.unit.test_graph_snapshot import graph_tables


@pytest.fixture(scope='module')
def projection():
    path = Path(os.environ['ETL_GRAPH_TEST_ENV'])
    assert path.name == 'test.env' and path.parent.name == 'neo4j'
    values = dotenv_values(path)
    assert values['NEO4J_URI_HOST'] == 'bolt://127.0.0.1:17687'
    s = Settings(_env_file=None, neo4j_enabled=True, neo4j_password=values['NEO4J_PASSWORD'],
                 neo4j_uri_host=values['NEO4J_URI_HOST'])
    driver = GraphDriver(s)
    driver.preflight()
    p = Neo4jProjection(driver)
    yield p
    driver.close()


def test_schema_exact_and_parameterized_merge_idempotent(projection):
    p = projection
    p.ensure_schema()
    expected = build_snapshot(graph_tables())
    p.merge(expected, batch_size=1)
    assert p.read() == expected
    p.merge(expected, batch_size=2)
    assert p.read() == expected
    p.verify_schema()


def test_transaction_rollback_preserves_graph(projection):
    before = projection.read()
    with pytest.raises(RuntimeError):
        with projection.driver.session(write=True) as s, s.begin_transaction() as tx:
            tx.run('CREATE (:Subject {id:$id})', id='rollback-only').consume()
            raise RuntimeError('TEST_ONLY_ROLLBACK')
    assert projection.read() == before


def test_orphan_and_id_rejection_before_write(projection):
    from data_sync_etl.domain.graph_contract import GraphSnapshot
    before = projection.read()
    with pytest.raises(GraphContractError):
        GraphSnapshot(frozenset({('Subject', '')}), frozenset())
    with pytest.raises(GraphContractError):
        GraphSnapshot(frozenset(), frozenset({('ContentUnit', 'missing', 'HAS_CHILD', 'ContentUnit', 'missing2')}))
    assert projection.read() == before


def test_empty_input_is_non_destructive_and_business_ids_are_parameters(projection):
    from data_sync_etl.domain.graph_contract import GraphSnapshot
    before = projection.read()
    projection.merge(GraphSnapshot(frozenset(), frozenset()))
    assert projection.read() == before
    weird = "x'\"})-[:ANY]->(x)"
    snapshot = GraphSnapshot(frozenset({('Subject', weird)}), frozenset())
    with projection.driver.session(write=True) as s, s.begin_transaction() as tx:
        projection.merge(snapshot, tx=tx)
        assert ('Subject', weird) in projection.read(tx=tx).nodes
        tx.rollback()
    assert projection.read() == before


@pytest.mark.parametrize('cypher', [
    'CREATE (:Unexpected {id:"invalid"})',
    'CREATE (:Subject {id:"unexpected-prop", title:"not allowed"})',
    'MATCH (a:ContentUnit),(b:ContentUnit) WITH a,b LIMIT 1 CREATE (a)-[:UNKNOWN]->(b)',
    'MATCH (a:ContentUnit),(b:ContentUnit) WITH a,b LIMIT 1 CREATE (a)-[:HAS_CHILD {x:1}]->(b)',
])
def test_extra_label_property_relation_rejected(projection, cypher):
    with projection.driver.session(write=True) as s, s.begin_transaction() as tx:
        tx.run(cypher).consume()
        with pytest.raises(GraphContractError):
            projection.read(tx=tx)
        tx.rollback()
