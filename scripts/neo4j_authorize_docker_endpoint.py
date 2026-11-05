"""Explicit host-to-Docker endpoint handoff with live read-only identity proofs.

Uses existing credentials only through their configured private files. Never
claims a nonempty database, changes graph data, or overwrites original receipts.
"""

import argparse
import json
import subprocess
from types import SimpleNamespace

from data_sync_etl.adapters.neo4j.driver import GraphDriver
from data_sync_etl.adapters.neo4j.projection import Neo4jProjection
from data_sync_etl.application.graph_sync import GraphSynchronizer
from data_sync_etl.config import Settings
from data_sync_etl.domain.graph_contract import GraphSnapshot

READ_PROOF = '''
import json
from data_sync_etl.config import Settings
from data_sync_etl.adapters.neo4j.driver import GraphDriver
from data_sync_etl.adapters.neo4j.projection import Neo4jProjection
s=Settings();d=GraphDriver(s)
try:
 d.preflight();p=Neo4jProjection(d);p.verify_schema();snapshot=p.read()
 print(json.dumps({'database_id':p.database_identity(),'uri':s.neo4j_uri_docker,
  'database':s.neo4j_database,'nodes':sorted(snapshot.nodes),'edges':sorted(snapshot.edges)}))
finally:d.close()
'''


class DockerProjectionProof:
    def __init__(self, container):
        self.container = container
        inspected = json.loads(subprocess.run(['docker', 'inspect', container],
            check=True, capture_output=True, text=True, timeout=15).stdout)[0]
        labels = inspected['Config']['Labels']
        if (labels.get('com.docker.compose.project') != 'etl-app'
                or labels.get('com.docker.compose.service') != 'api'):
            raise ValueError('GRAPH_DOCKER_TARGET_UNPROVEN')
        proof = self._read()
        self.driver = SimpleNamespace(settings=SimpleNamespace(neo4j_runtime='docker',
            neo4j_uri_docker=proof['uri'], neo4j_database=proof['database']))

    def _read(self):
        result = subprocess.run(['docker', 'exec', '-i', self.container, 'python', '-'],
            input=READ_PROOF, capture_output=True, text=True, timeout=30, check=True)
        return json.loads(result.stdout)

    def database_identity(self):
        return self._read()['database_id']

    def verify_schema(self):
        self._read()  # The remote proof fails before output if schema differs.

    def read(self):
        result = self._read()
        return GraphSnapshot(frozenset(map(tuple, result['nodes'])),
                             frozenset(map(tuple, result['edges'])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--container', default='etl-app-api-1')
    parser.add_argument('--apply', action='store_true', required=True)
    args = parser.parse_args()
    driver = None
    try:
        settings = Settings()
        if settings.neo4j_runtime != 'host':
            raise ValueError('GRAPH_ORIGINAL_HOST_ENDPOINT_REQUIRED')
        driver = GraphDriver(settings)
        driver.preflight()
        sync = GraphSynchronizer(None, Neo4jProjection(driver), settings.neo4j_state_dir)
        result = sync.authorize_same_database_endpoint(DockerProjectionProof(args.container))
        print(json.dumps(result))
        return 0
    except Exception:
        print(json.dumps({'status': 'GRAPH_ENDPOINT_HANDOFF_BLOCKED'}))
        return 1
    finally:
        if driver is not None:
            driver.close()


if __name__ == '__main__':
    raise SystemExit(main())
