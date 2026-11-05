"""Graph CLI deliberately bypasses Container: no OCR/SOURCE writer composition."""

import json

from sqlalchemy import create_engine

from data_sync_etl.adapters.neo4j.driver import GraphDriver, GraphUnavailable
from data_sync_etl.adapters.persistence.graph_snapshot import (
    MasterGraphReader,
    verify_bag_roots,
    verify_content_mapping,
)
from data_sync_etl.application.graph_sync import GraphSynchronizer
from data_sync_etl.config import Settings
from data_sync_etl.domain.graph_contract import GraphContractError
from data_sync_etl.workers.lifecycle import tick, worker_lifecycle


def coverage(settings, snapshot):
    from pymongo import MongoClient
    with MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=5000,
                     connectTimeoutMS=5000, socketTimeoutMS=5000) as client:
        db = client[settings.mongo_database]
        bags = verify_bag_roots(snapshot, db.content_bags.find(
            {'is_current': True}, {'_id': 1, 'resource_version_id': 1}).max_time_ms(5000))
        content = verify_content_mapping(snapshot, db.mongo_content_units.find(
            {'is_current': True}, {'_id': 1, 'content_unit_id': 1, 'resource_version_id': 1}).max_time_ms(5000))
        return {**bags, 'content_mapping': content}


def run(args, *, settings=None):
    with worker_lifecycle() as stop:
        return _run(args, settings=settings, stop=stop)


def _run(args, *, settings, stop):
    driver = engine = None
    try:
        s = settings or Settings()
        driver = GraphDriver(s)
        driver.preflight()
        from data_sync_etl.adapters.neo4j.projection import Neo4jProjection
        projection = Neo4jProjection(driver)
        if args.command == 'graph-status':
            actual = projection.read()
            print(json.dumps({'status': 'GRAPH_CONNECTED', **actual.counts()}))
            return 0
        engine = create_engine(s.database_url, hide_parameters=True)
        reader = MasterGraphReader(engine, batch_size=s.neo4j_batch_size)
        sync = GraphSynchronizer(reader, projection, s.neo4j_state_dir)
        if args.command == 'graph-preflight':
            result = (sync.claim_empty() if args.claim_empty else {'status': 'GRAPH_CONNECTED'})
            print(json.dumps(result))
            return 0
        unavailable_attempts = 0
        while not stop.is_set():
            snapshot = reader.read()
            bags = coverage(s, snapshot)
            if bags['missing_or_wrong_version'] or bags.get('content_mapping', {}).get('mapping_drift'):
                print(json.dumps({'status': 'GRAPH_BAG_MAPPING_DRIFT', 'bags': bags}))
                return 1
            plan_path = s.neo4j_state_dir / 'dry-run.json'
            apply = getattr(args, 'apply', False) or args.command == 'run-graph-worker'
            if apply and args.command != 'run-graph-worker':
                try:
                    plan = json.loads(plan_path.read_text(encoding='utf-8'))
                    if (plan['fingerprint'] != snapshot.fingerprint
                            or plan['database_id'] != projection.database_identity()):
                        raise ValueError()
                except (OSError, ValueError, KeyError):
                    raise GraphContractError('GRAPH_FRESH_DRY_RUN_REQUIRED') from None
            # Pin the exact validated snapshot used for bag coverage, not a second scan.
            class FrozenReader:
                def __init__(self, value):
                    self.value = value

                def read(self):
                    return self.value
            sync.reader = FrozenReader(snapshot)
            try:
                result = sync.sync(apply=apply, expected_fingerprint=snapshot.fingerprint)
            except GraphUnavailable:
                if args.command != 'run-graph-worker' or args.once:
                    raise
                print(json.dumps({'status': 'GRAPH_UNAVAILABLE_RETRY'}), flush=True)
                unavailable_attempts += 1
                if unavailable_attempts >= 3:
                    return 1  # Supervisor restarts; never heartbeat a failed refresh.
                stop.wait(s.neo4j_sync_interval_seconds)
                continue
            unavailable_attempts = 0
            result['bags'] = bags
            if getattr(args, 'dry_run', False):
                s.neo4j_state_dir.mkdir(parents=True, exist_ok=True)
                plan_path.write_text(json.dumps({'fingerprint': snapshot.fingerprint,
                    'database_id': projection.database_identity()}), encoding='utf-8')
            print(json.dumps(result), flush=True)
            if args.command == 'run-graph-worker':
                tick()
            if args.command != 'run-graph-worker' or args.once:
                return 1 if args.command == 'graph-diff' and result['status'] != 'MATCH' else 0
            stop.wait(s.neo4j_sync_interval_seconds)
        return 0
    except (GraphContractError, GraphUnavailable) as exc:
        print(json.dumps({'status': str(exc)}))
        return 1
    except Exception:
        print(json.dumps({'status': 'GRAPH_OPERATION_FAILED'}))
        return 1
    finally:
        if driver:
            driver.close()
        if engine:
            engine.dispose()
