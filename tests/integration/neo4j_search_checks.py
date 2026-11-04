"""Required isolated Search E2E, using test-only SQLite/Mongo doubles and Neo4j.

Run explicitly alongside neo4j_checks.py with ETL_GRAPH_TEST_ENV set.
"""

import json
import subprocess
from time import monotonic
from types import SimpleNamespace

from data_sync_etl.adapters.neo4j.resolver import Neo4jResolver
from data_sync_etl.adapters.persistence.graph_mapping import PostgresGraphMapping
from data_sync_etl.adapters.persistence.graph_snapshot import MasterGraphReader
from data_sync_etl.application.graph_search import GraphSearchAdvisor
from data_sync_etl.application.graph_sync import GraphSynchronizer
from data_sync_etl.domain.graph_contract import GraphSnapshot
from data_sync_etl.domain.search_access import SearchAccess
from tests.integration.neo4j_checks import projection as projection
from tests.unit.test_search import searchable as searchable


def test_complete_search_graph_parity_privacy_stale_and_actual_outage(
        searchable, projection, tmp_path, monkeypatch):
    c, version = searchable
    original_graph = projection.read()
    snapshot = MasterGraphReader(c.engine, batch_size=1).read()
    reader = SimpleNamespace(read=lambda: snapshot)
    sync = GraphSynchronizer(reader, projection, tmp_path)
    identity = sync._identity()
    sync._receipt(identity).write_text(json.dumps(identity), encoding='utf-8')
    monkeypatch.setattr(projection.driver.settings, 'neo4j_sync_enabled', True)
    sync.sync(apply=True)
    graph = Neo4jResolver(projection.driver)
    advisor = GraphSearchAdvisor(graph, PostgresGraphMapping(c.engine), c.settings)
    c.search.graph_advisor = advisor
    baseline, elapsed = {}, {}
    compose = ['docker', 'compose', '-p', 'etl-neo4j-test', '--env-file', '.local/neo4j/test.env',
               '-f', 'deploy/compose.neo4j.yml', '-f', 'deploy/compose.neo4j.test.yml']
    stopped = False
    try:
        for mode in ['disabled', 'shadow', 'active']:
            c.settings.neo4j_search_enabled = mode != 'disabled'
            c.settings.neo4j_search_mode = 'active' if mode == 'active' else 'shadow'
            started = monotonic()
            for method in ['bow', 'vector', 'hybrid']:
                response = c.search.search('page', method)
                assert response['total'] > 0
                if mode == 'disabled':
                    baseline[method] = response
                else:
                    assert response == baseline[method]
            elapsed[mode] = monotonic()-started
        assert advisor.metrics['shadow'] == advisor.metrics['active_resolution'] == 3
        # One batched graph call and three bounded PG queries per Search, no N+1.
        assert elapsed['active'] <= elapsed['disabled'] + 3*c.settings.neo4j_query_timeout_seconds
        before = dict(advisor.metrics)
        assert c.search.search('page', access=SearchAccess())['total'] == 0
        assert dict(advisor.metrics) == before
        missing = next(e for e in snapshot.edges if e[2] == 'CONTAINS'
                       and e[4] == baseline['bow']['items'][0]['bag_id'])
        reader.read = lambda: GraphSnapshot(snapshot.nodes, snapshot.edges - {missing})
        sync.sync(apply=True)
        assert c.search.search('page', 'bow') == baseline['bow']
        assert advisor.metrics['unavailable_or_stale_fallback'] > 0
        reader.read = lambda: snapshot
        sync.sync(apply=True)
        # DRAFT and deletion still use existing Search authority, never graph visibility.
        with c.uow() as repo:
            resource = repo.get('master_learning_resources', version['resource_id'])
            fixture_owner = resource['created_by'] or 'test-owner'
            repo.update('master_learning_resources', resource['id'], {
                'publication_status':'DRAFT', 'created_by':fixture_owner})
        admin = SearchAccess('authenticated_admin', actor_id='master:test-admin')
        stranger = SearchAccess('authenticated_user', actor_id='master:test-stranger')
        owner = SearchAccess('authenticated_user', actor_id='master:test-owner',
                             resource_ids=frozenset({resource['id']}), owner_id=fixture_owner,
                             allow_owned_unreviewed=True)
        assert c.search.search('page', access=stranger)['total'] == 0
        assert c.search.search('page', access=admin)['total'] > 0
        assert c.search.search('page', access=owner)['total'] > 0
        with c.uow() as repo:
            repo.update('master_learning_resources', resource['id'], {'publication_status':resource['publication_status']})
        subprocess.run([*compose, 'stop'], check=True, capture_output=True, timeout=60)
        stopped = True
        for method in ['bow', 'vector', 'hybrid']:
            assert c.search.search('page', method) == baseline[method]
        previous_active = advisor.metrics['active_resolution']
        original_driver = projection.driver._driver
        subprocess.run([*compose, 'up', '-d', '--wait', '--wait-timeout', '120'],
                       check=True, capture_output=True, timeout=150)
        stopped = False
        for _ in range(3):
            assert c.search.search('page', 'bow') == baseline['bow']
            if advisor.metrics['active_resolution'] > previous_active:
                break
        assert advisor.metrics['active_resolution'] > previous_active
        assert projection.driver._driver is original_driver
        with c.uow() as repo:
            repo.update('master_learning_resources', resource['id'], {'is_active':False})
        assert c.search.search('page', access=admin)['total'] == 0
        assert advisor.metrics['unavailable_or_stale_fallback'] >= 4
    finally:
        advisor.close()
        c.search.graph_advisor = None
        if stopped:
            subprocess.run([*compose, 'up', '-d', '--wait', '--wait-timeout', '120'],
                           check=True, capture_output=True, timeout=150)
        reader.read = lambda: original_graph
        sync.sync(apply=True)
    assert projection.read() == original_graph
