"""Read-only traversal of already authorized ContentBag root business IDs."""

from data_sync_etl.domain.graph_contract import GraphContractError, graph_id

RESOLVE = '''
UNWIND $candidate_ids AS cid
MATCH (cu:ContentUnit {id:cid})<-[:CONTAINS]-(rv:ResourceVersion)<-[:HAS_VERSION]-(lr:LearningResource)
OPTIONAL MATCH (lr)-[:HAS_SUBJECT]->(s:Subject)
OPTIONAL MATCH (lr)-[:FOR_GRADE]->(g:GradeLevel)
RETURN cu.id AS content_unit_id, rv.id AS resource_version_id, lr.id AS resource_id,
       collect(DISTINCT s.id) AS subject_ids, collect(DISTINCT g.id) AS grade_ids
ORDER BY content_unit_id
LIMIT $limit
'''


def candidate_ids(values, maximum=100):
    if not values or len(values) > maximum or len(set(values)) != len(values):
        raise GraphContractError('GRAPH_CANDIDATE_LIMIT')
    return [graph_id(value) for value in values]


class Neo4jResolver:
    def __init__(self, driver):
        self.driver = driver

    def resolve(self, values):
        ids = candidate_ids(values)
        from neo4j import Query
        with self.driver.session() as session:
            rows = [dict(row) for row in session.run(Query(RESOLVE,
                timeout=self.driver.settings.neo4j_query_timeout_seconds),
                candidate_ids=ids, limit=len(ids)+1)]
        return rows
