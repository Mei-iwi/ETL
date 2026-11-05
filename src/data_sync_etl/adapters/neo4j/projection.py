"""Fixed Cypher whitelist. Only Neo4j can be mutated by this adapter."""

from data_sync_etl.domain.graph_contract import (
    LABELS,
    RELATIONS,
    GraphContractError,
    GraphSnapshot,
    graph_id,
)
from neo4j import Query, unit_of_work

# All interpolation below is from this module's immutable contract, never input.
CONSTRAINTS = {
    label: f'CREATE CONSTRAINT etl_{label}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE'
    for label in LABELS
}
NODE_MERGE = {label: f'UNWIND $ids AS id MERGE (n:{label} {{id:id}})' for label in LABELS}
EDGE_MERGE = {
    shape: (f'UNWIND $edges AS e MATCH (a:{shape[0]} {{id:e[0]}}), '
            f'(b:{shape[2]} {{id:e[1]}}) MERGE (a)-[:{shape[1]}]->(b)')
    for shape in RELATIONS
}
NODE_DELETE = {label: f'UNWIND $ids AS id MATCH (n:{label} {{id:id}}) DELETE n' for label in LABELS}
EDGE_DELETE = {
    shape: (f'UNWIND $edges AS e MATCH (a:{shape[0]} {{id:e[0]}})'
            f'-[r:{shape[1]}]->(b:{shape[2]} {{id:e[1]}}) DELETE r')
    for shape in RELATIONS
}


class Neo4jProjection:
    def __init__(self, driver):
        self.driver = driver
        self.timeout = driver.settings.neo4j_query_timeout_seconds

    def query(self, cypher):
        return Query(cypher, timeout=self.timeout)

    def ensure_schema(self):
        with self.driver.session(write=True) as session:
            for query in CONSTRAINTS.values():
                session.run(self.query(query)).consume()
        self.verify_schema()

    def verify_schema(self):
        with self.driver.session() as session:
            rows = list(session.run(self.query(
                'SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties '
                'RETURN name, type, labelsOrTypes, properties')))
        expected = {('etl_'+label+'_id', 'UNIQUENESS', (label,), ('id',)) for label in LABELS}
        actual = {(r['name'], r['type'], tuple(r['labelsOrTypes']), tuple(r['properties'])) for r in rows}
        if actual != expected:
            raise GraphContractError('GRAPH_SCHEMA_DIFF')

    def database_identity(self):
        with self.driver.session() as session:
            return str(session.run(self.query('CALL db.info() YIELD id RETURN id')).single()['id'])

    def read(self, *, tx=None):
        if tx is None:
            with self.driver.session() as session:
                return session.execute_read(unit_of_work(timeout=self.timeout)(lambda t: self.read(tx=t)))
        nodes = []
        for row in tx.run('MATCH (n) RETURN labels(n) AS labels, properties(n) AS props'):
            if (len(row['labels']) != 1 or row['labels'][0] not in LABELS
                    or set(row['props']) != {'id'}):
                raise GraphContractError('GRAPH_UNEXPECTED_NODE')
            nodes.append((row['labels'][0], graph_id(row['props']['id'])))
        edges = []
        for row in tx.run(
                'MATCH (a)-[r]->(b) RETURN labels(a) AS al, a.id AS aid, type(r) AS rel, '
                'labels(b) AS bl, b.id AS bid, properties(r) AS props'):
            if len(row['al']) != 1 or len(row['bl']) != 1 or row['props']:
                raise GraphContractError('GRAPH_UNEXPECTED_EDGE')
            edges.append((row['al'][0], row['aid'], row['rel'], row['bl'][0], row['bid']))
        if len(set(nodes)) != len(nodes) or len(set(edges)) != len(edges):
            raise GraphContractError('GRAPH_DUPLICATE_DATA')
        return GraphSnapshot(frozenset(nodes), frozenset(edges))

    def merge(self, snapshot, *, batch_size=200, tx=None, added_nodes=None, added_edges=None):
        # Revalidate even when a caller bypasses the normal MASTER reader.
        GraphSnapshot(snapshot.nodes, snapshot.edges)
        if not 1 <= batch_size <= 1000:
            raise GraphContractError('GRAPH_BATCH_INVALID')
        if tx is None:
            with self.driver.session(write=True) as session:
                return session.execute_write(unit_of_work(timeout=self.driver.settings.neo4j_sync_timeout_seconds)(
                    lambda t: self.merge(snapshot, batch_size=batch_size, tx=t,
                                         added_nodes=added_nodes, added_edges=added_edges)))
        nodes = snapshot.nodes if added_nodes is None else added_nodes
        relationships = snapshot.edges if added_edges is None else added_edges
        if not set(nodes) <= snapshot.nodes or not set(relationships) <= snapshot.edges:
            raise GraphContractError('GRAPH_DELTA_INVALID')
        for label in LABELS:
            ids = sorted(key for kind, key in nodes if kind == label)
            for start in range(0, len(ids), batch_size):
                tx.run(NODE_MERGE[label], ids=ids[start:start+batch_size]).consume()
        for shape in RELATIONS:
            edges = sorted((e[1], e[4]) for e in relationships if (e[0], e[2], e[3]) == shape)
            for start in range(0, len(edges), batch_size):
                tx.run(EDGE_MERGE[shape], edges=edges[start:start+batch_size]).consume()

    def remove(self, nodes, edges, *, tx, batch_size=200):
        # All shapes were audited by read(); never DETACH DELETE or wipe a database.
        for shape in RELATIONS:
            pairs = sorted((e[1], e[4]) for e in edges if (e[0], e[2], e[3]) == shape)
            for start in range(0, len(pairs), batch_size):
                tx.run(EDGE_DELETE[shape], edges=pairs[start:start+batch_size]).consume()
        for label in LABELS:
            ids = sorted(key for kind, key in nodes if kind == label)
            for start in range(0, len(ids), batch_size):
                tx.run(NODE_DELETE[label], ids=ids[start:start+batch_size]).consume()
