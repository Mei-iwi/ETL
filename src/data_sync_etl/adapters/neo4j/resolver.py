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

    def related(self, resource_id, allowed_ids):
        ids = candidate_ids(allowed_ids)
        if resource_id not in ids:
            raise GraphContractError('GRAPH_CANDIDATE_LIMIT')
        from neo4j import Query
        query = '''
        MATCH (seed:LearningResource {id:$resource_id})
        UNWIND $allowed_ids AS other_id
        MATCH (other:LearningResource {id:other_id})
        WHERE other <> seed
        OPTIONAL MATCH (seed)-[:HAS_SUBJECT]->(s:Subject)<-[:HAS_SUBJECT]-(other)
        WITH seed, other, collect(DISTINCT s.id) AS subjects
        OPTIONAL MATCH (seed)-[:FOR_GRADE]->(g:GradeLevel)<-[:FOR_GRADE]-(other)
        WITH other, subjects, collect(DISTINCT g.id) AS grades
        WHERE size(subjects)>0 OR size(grades)>0
        RETURN other.id AS resource_id, subjects AS subject_ids, grades AS grade_ids
        ORDER BY resource_id LIMIT 100
        '''
        with self.driver.session() as session:
            rows = [dict(row) for row in session.run(Query(query,
                timeout=self.driver.settings.neo4j_query_timeout_seconds),
                resource_id=resource_id, allowed_ids=ids)]
        return [{**row, 'subject_ids': sorted(row['subject_ids']),
                 'grade_ids': sorted(row['grade_ids'])} for row in rows]

    def resolve(self, values):
        ids = candidate_ids(values)
        from neo4j import Query
        with self.driver.session() as session:
            rows = [dict(row) for row in session.run(Query(RESOLVE,
                timeout=self.driver.settings.neo4j_query_timeout_seconds),
                candidate_ids=ids, limit=len(ids)+1)]
        return rows
