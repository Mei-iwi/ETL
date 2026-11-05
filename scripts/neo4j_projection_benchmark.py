"""Read-only live scan plus bounded 1x/5x/10x writes to a proven TEST graph.

No SOURCE/MASTER/Mongo mutations. Synthetic graph IDs are namespaced digests,
never evidence of production identity. Results are private and contain no IDs.
"""

import json
import subprocess
import tracemalloc
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
from statistics import median
from time import perf_counter

from dotenv import dotenv_values
from sqlalchemy import create_engine

from data_sync_etl.adapters.neo4j.driver import GraphDriver
from data_sync_etl.adapters.neo4j.projection import Neo4jProjection
from data_sync_etl.adapters.neo4j.resolver import Neo4jResolver
from data_sync_etl.adapters.persistence.graph_snapshot import MasterGraphReader
from data_sync_etl.cli.graph import coverage
from data_sync_etl.config import Settings
from data_sync_etl.domain.graph_contract import GraphSnapshot


def p95(values):
    return sorted(values)[max(0, int(len(values) * .95 + .9999) - 1)]


def main():
    root = Path('.local/neo4j/active-20261105')
    values = dotenv_values(root / 'related-test/test.env')
    inspected = json.loads(subprocess.check_output(
        ['docker', 'inspect', 'etl-neo4j-related-neo4j-1'], text=True))[0]
    assert inspected['Config']['Labels']['com.docker.compose.project'] == 'etl-neo4j-related'
    assert inspected['NetworkSettings']['Ports']['7687/tcp'][0]['HostPort'] == '27687'
    assert values['NEO4J_URI_HOST'] == 'bolt://127.0.0.1:27687'
    settings = Settings()
    engine = create_engine(settings.database_url, hide_parameters=True)
    test_settings = Settings(_env_file=None, neo4j_enabled=True,
        neo4j_uri_host=values['NEO4J_URI_HOST'], neo4j_password_file=values['NEO4J_PASSWORD_FILE'],
        neo4j_query_timeout_seconds=30, neo4j_sync_timeout_seconds=120)
    driver = GraphDriver(test_settings)
    projection = Neo4jProjection(driver)
    report = {'scope': 'live read-only scan; isolated synthetic projection and graph queries',
              'source_writes': 0, 'load': [], 'queries': []}
    original = None
    try:
        driver.preflight()
        projection.verify_schema()
        original = projection.read()
        tracemalloc.start()
        start = perf_counter()
        snapshot = MasterGraphReader(engine).read()
        bag_coverage = coverage(settings, snapshot)
        elapsed = perf_counter() - start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        report['live_scan'] = {'seconds': elapsed, 'nodes': len(snapshot.nodes),
            'edges': len(snapshot.edges), 'records_per_second': (len(snapshot.nodes) + len(snapshot.edges)) / elapsed,
            'python_peak_bytes': peak, 'bag_mapping': bag_coverage}
        for scale in (1, 5, 10):
            def key(copy, label, value):
                return 'T' + str(copy).zfill(2) + sha256((label + '\0' + value).encode()).hexdigest()[:26]
            nodes = frozenset((label, key(copy, label, value))
                for copy in range(scale) for label, value in snapshot.nodes)
            edges = frozenset((left, key(copy, left, a), rel, right, key(copy, right, b))
                for copy in range(scale) for left, a, rel, right, b in snapshot.edges)
            expanded = GraphSnapshot(original.nodes | nodes, original.edges | edges)
            tracemalloc.start()
            start = perf_counter()
            projection.merge(expanded)
            written_seconds = perf_counter() - start
            assert projection.read() == expanded
            again = perf_counter()
            projection.merge(expanded)
            replay_seconds = perf_counter() - again
            assert projection.read() == expanded
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            report['load'].append({'scale': scale, 'synthetic_nodes': len(nodes),
                'synthetic_edges': len(edges), 'write_seconds': written_seconds,
                'replay_seconds': replay_seconds, 'python_peak_bytes': peak, 'exact_parity': True})
            with driver.session(write=True) as session:
                session.execute_write(lambda tx, ns=nodes, es=edges: projection.remove(ns, es, tx=tx))
            assert projection.read() == original
        resolver = Neo4jResolver(driver)
        for _ in range(5):
            resolver.related('seed', ['seed', 'other'])
        for clients in (1, 5, 10):
            def query(_):
                start = perf_counter()
                result = resolver.related('seed', ['seed', 'other'])
                assert len(result) == 1 and result[0]['resource_id'] == 'other'
                return (perf_counter() - start) * 1000
            with ThreadPoolExecutor(max_workers=clients) as pool:
                times = list(pool.map(query, range(20)))
            report['queries'].append({'clients': clients, 'samples': len(times),
                'p50_ms': median(times), 'p95_ms': p95(times), 'max_ms': max(times), 'errors': 0})
        report['status'] = 'PASS'
    except Exception as exc:
        report.update(status='FAIL', error_type=type(exc).__name__)
    finally:
        if tracemalloc.is_tracing():
            tracemalloc.stop()
        if original is not None:
            report['test_graph_restored'] = projection.read() == original
        driver.close()
        engine.dispose()
        (root / 'projection-benchmark.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'live_writes': 0}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
