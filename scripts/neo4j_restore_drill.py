"""Reconstruct a read-only live graph snapshot, then dump/load isolated copies.

Community dump requires an offline database. This drill never stops live Neo4j
or mounts its data volume. It validates logical graph recovery and an official
dump/load of the reconstructed donor, not a physical backup of the live store.
Private dumps and isolated volumes are retained; no delete/overwrite is used.
"""

import json
import os
import secrets
import subprocess
from pathlib import Path
from time import perf_counter

from data_sync_etl.adapters.neo4j.driver import GraphDriver
from data_sync_etl.adapters.neo4j.projection import Neo4jProjection
from data_sync_etl.config import Settings


def main():
    root = Path('.local/neo4j/active-20261105/restore-drill').resolve()
    root.mkdir(parents=True, exist_ok=True)
    run_id = secrets.token_hex(4)
    work = root / run_id
    work.mkdir()
    backups = work / 'backups'
    backups.mkdir()
    password = secrets.token_urlsafe(32)
    auth, password_file = work / 'auth.secret', work / 'password.secret'
    auth.write_text('neo4j/' + password, encoding='utf-8')
    password_file.write_text(password, encoding='utf-8')
    for path in (work, backups, auth, password_file):
        os.chmod(path, 0o700 if path.is_dir() else 0o600)
    report = {'run_id': run_id, 'status': 'BLOCKED', 'live_volume_mounted': False,
              'live_server_stopped': False, 'scope': 'logical snapshot; isolated donor dump/load'}
    projects, drivers = [], []
    started = perf_counter()

    def docker(args, timeout=180):
        result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout)
        if result.returncode:
            raise RuntimeError('GRAPH_DRILL_DOCKER_FAILED')
        return result.stdout

    def compose(role, bolt, http):
        project = 'etl-neo4j-drill-' + role + '-' + run_id
        env = work / (role + '.env')
        env.write_text('\n'.join([
            'NEO4J_AUTH_FILE=' + auth.as_posix(), 'NEO4J_BOLT_PORT=' + str(bolt),
            'NEO4J_HTTP_PORT=' + str(http), 'NEO4J_DRILL_NETWORK=' + project + '-graph',
            'NEO4J_DRILL_BACKUP_DIR=' + backups.as_posix(), 'NEO4J_PAGECACHE_SIZE=128M',
        ]) + '\n', encoding='utf-8')
        args = ['compose', '--project-name', project, '--env-file', str(env),
                '-f', 'deploy/compose.neo4j.yml', '-f', 'deploy/compose.neo4j.drill.yml',
                '-f', 'deploy/compose.neo4j.restore.yml']
        # Unique names: refuse to reuse an existing data volume or container.
        existing = subprocess.run(['docker', 'volume', 'inspect', project + '_graph-data'],
                                  capture_output=True)
        if existing.returncode == 0:
            raise RuntimeError('GRAPH_DRILL_TARGET_NOT_EMPTY')
        projects.append((project, args))
        return args

    def projection(port):
        settings = Settings(_env_file=None, neo4j_enabled=True,
            neo4j_uri_host='bolt://127.0.0.1:' + str(port),
            neo4j_password_file=password_file, neo4j_sync_timeout_seconds=120,
            neo4j_query_timeout_seconds=15)
        driver = GraphDriver(settings)
        drivers.append(driver)
        driver.preflight()
        return Neo4jProjection(driver)

    try:
        live_driver = GraphDriver(Settings())
        drivers.append(live_driver)
        live_driver.preflight()
        live = Neo4jProjection(live_driver)
        live.verify_schema()
        snapshot = live.read()
        assert live.read() == snapshot
        donor_args = compose('donor', 28687, 28474)
        restored_args = compose('restored', 38687, 38474)
        docker(donor_args + ['up', '-d', '--wait'])
        donor = projection(28687)
        assert not donor.read().nodes
        donor.ensure_schema()
        donor.merge(snapshot)
        assert donor.read() == snapshot
        donor.driver.close()
        docker(donor_args + ['stop', 'neo4j'])
        docker(donor_args + ['run', '--rm', '--no-deps', '--user', '7474:7474',
            '--entrypoint', 'neo4j-admin', 'neo4j', 'database', 'dump', 'neo4j',
            '--to-path=/backups'])
        dump = backups / 'neo4j.dump'
        assert dump.is_file() and dump.stat().st_size > 0
        os.chmod(dump, 0o600)
        docker(restored_args + ['run', '--rm', '--no-deps', '--user', '7474:7474',
            '--entrypoint', 'neo4j-admin', 'neo4j', 'database', 'load', 'neo4j',
            '--from-path=/backups'])
        docker(restored_args + ['up', '-d', '--wait'])
        restored = projection(38687)
        restored.verify_schema()
        assert restored.read() == snapshot
        restored.driver.close()
        docker(restored_args + ['restart', 'neo4j'])
        docker(restored_args + ['up', '-d', '--wait'])
        restarted = projection(38687)
        restarted.verify_schema()
        assert restarted.read() == snapshot
        # Record live drift truthfully, never modify live data to force equality.
        live_unchanged = live.read() == snapshot
        report.update(status='PASS', graph_fingerprint=snapshot.fingerprint,
            counts=snapshot.counts(), live_unchanged=live_unchanged,
            dump_bytes=dump.stat().st_size, restart_parity=True)
    except Exception as exc:
        report['error_type'] = type(exc).__name__
    finally:
        for driver in drivers:
            driver.close()
        for _project, args in projects:
            try:
                docker(args + ['stop', 'neo4j'], timeout=60)
            except Exception:
                report['cleanup_status'] = 'ISOLATED_STOP_REQUIRED'
        report['seconds'] = round(perf_counter() - started, 3)
        (work / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        (root / 'latest-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'scope': report['scope'],
                      'live_server_stopped': False}))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
