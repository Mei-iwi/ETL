"""Explicit isolated graph traversal checks; never collected as a live test."""

import json
import os
import subprocess
from pathlib import Path
from time import monotonic, sleep
from types import SimpleNamespace

from dotenv import dotenv_values

from data_sync_etl.adapters.neo4j.driver import GraphDriver
from data_sync_etl.adapters.neo4j.projection import Neo4jProjection
from data_sync_etl.adapters.neo4j.resolver import Neo4jResolver
from data_sync_etl.application.related_learning import RelatedLearning
from data_sync_etl.config import Settings
from data_sync_etl.domain.graph_contract import GraphSnapshot
from data_sync_etl.domain.search_access import SearchAccess


def test_real_traversal_and_active_suggestions_are_allowlisted():
    values = dotenv_values(Path(os.environ['ETL_RELATED_TEST_ENV']))
    assert values['NEO4J_URI_HOST'] == 'bolt://127.0.0.1:27687'
    inspected = json.loads(subprocess.check_output(
        ['docker', 'inspect', 'etl-neo4j-related-neo4j-1'], text=True))[0]
    assert inspected['Config']['Labels']['com.docker.compose.project'] == 'etl-neo4j-related'
    assert inspected['NetworkSettings']['Ports']['7687/tcp'][0]['HostPort'] == '27687'
    settings = Settings(_env_file=None, neo4j_enabled=True, neo4j_search_enabled=True,
        neo4j_search_mode='active', neo4j_uri_host=values['NEO4J_URI_HOST'],
        neo4j_password_file=values['NEO4J_PASSWORD_FILE'])
    driver = GraphDriver(settings)
    service = None
    stopped = False
    try:
        projection = Neo4jProjection(driver)
        projection.ensure_schema()
        nodes = frozenset([('LearningResource', 'seed'), ('LearningResource', 'other'),
                           ('LearningResource', 'private'), ('Subject', 's'), ('GradeLevel', 'g')])
        edges = frozenset((('LearningResource', key, relation, label, target)
                          for key in ('seed', 'other', 'private')
                          for relation, label, target in [('HAS_SUBJECT', 'Subject', 's'),
                                                         ('FOR_GRADE', 'GradeLevel', 'g')]))
        snapshot = GraphSnapshot(nodes, edges)
        projection.merge(snapshot)
        resolver = Neo4jResolver(driver)
        assert resolver.related('seed', ['seed', 'other']) == [{
            'resource_id': 'other', 'subject_ids': ['s'], 'grade_ids': ['g'],
        }]
        authority = SimpleNamespace(read=lambda *args: {
            key: {'title': key.title(), 'version_id': 'v-' + key, 'source_hash': 'a' * 64,
                  'subject_ids': ['s'], 'grade_ids': ['g']} for key in ('seed', 'other')})
        service = RelatedLearning(resolver, authority, settings)
        result = service.run('seed', SearchAccess('authenticated_admin'))
        assert len(result) == 1 and result[0]['resource_id'] == 'other'
        assert result[0]['url'] == '/library/other'
        evidence = Path('.local/neo4j/active-20261105/related-fixture.json')
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(json.dumps({'items': result}), encoding='utf-8')
        settings.neo4j_search_mode = 'shadow'
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
        assert projection.read() == snapshot
        settings.neo4j_search_mode = 'active'
        subprocess.run(['docker', 'stop', 'etl-neo4j-related-neo4j-1'],
                       check=True, capture_output=True, timeout=30)
        stopped = True
        start = monotonic()
        assert service.run('seed', SearchAccess('authenticated_admin')) == []
        assert monotonic() - start < 4
        subprocess.run(['docker', 'start', 'etl-neo4j-related-neo4j-1'],
                       check=True, capture_output=True, timeout=30)
        stopped = False
        for _ in range(30):
            try:
                driver.preflight()
                break
            except Exception:
                sleep(1)
        else:
            raise AssertionError('ISOLATED_GRAPH_RECOVERY_FAILED')
        assert service.run('seed', SearchAccess('authenticated_admin')) == result
        assert projection.read() == snapshot
        evidence.with_name('related-outage.json').write_text(json.dumps({
            'active_graph_down_items': 0, 'recovered_items': len(result),
            'graph_unchanged': True, 'scope': 'isolated graph; no live credentials',
        }), encoding='utf-8')
    finally:
        if stopped:
            subprocess.run(['docker', 'start', 'etl-neo4j-related-neo4j-1'],
                           check=True, capture_output=True, timeout=30)
        if service is not None:
            service.close()
        driver.close()
