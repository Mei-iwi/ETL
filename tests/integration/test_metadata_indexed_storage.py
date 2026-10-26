"""Physical PG/Mongo evidence; synthetic resources only, existing test guards."""
import json
import os
from pathlib import Path
from time import perf_counter, process_time

import pytest
from pymongo import MongoClient
from sqlalchemy import event

from data_sync_etl.adapters.mongo.metadata_search import (
    CANDIDATE_INDEX,
    MongoMetadataIndex,
    install,
)
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.search_access import SearchAccess
from tests.unit.test_metadata_indexed_candidates import index_pages, seed
from tests.unit.test_metadata_search import metadata as metadata
from tests.unit.test_metadata_search import search

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


@pytest.mark.parametrize('count', [201, 500, 2000, 5000, 10000])
def test_physical_indexed_metadata_scale(metadata, mongo_test_database, count):
    c, _, _, service = metadata
    uri, name = mongo_test_database
    sql = [0]
    def before(*_):
        sql[0] += 1
    event.listen(c.engine, 'before_cursor_execute', before)
    with MongoClient(uri) as client:
        database = client[name]
        install(database, candidates=True)
        service.store = MongoMetadataIndex(database)
        c.settings.search_metadata_indexed_candidates_enabled = True
        seed(c, count)
        started = perf_counter()
        index_pages(c, service)
        index_seconds = perf_counter() - started
        durations, statements, ids = [], [], None
        cpu = process_time()
        for _ in range(5):
            started, initial = perf_counter(), sql[0]
            response = search(c, 'sentinel')
            durations.append(perf_counter() - started)
            statements.append(sql[0] - initial)
            ids = [row['resource_id'] for row in response['items']]
            assert ids == ['resource-1'] and response['total'] == 1
            assert response['branch_status']['metadata_lexical'] == 'READY'
        duration_cpu = process_time() - cpu
        snapshot = service.indexed_snapshots({}, SearchAccess('authenticated_admin'))
        query = {'status': 'READY', 'resource_version_id': {'$in': sorted(snapshot.ready_keys)},
                 'candidate_terms': {'$in': ['sentinel']}}
        plan = database.command('explain', {'find': service.store.collection.name, 'filter': query,
            'projection': {'resource_version_id': 1}, 'hint': CANDIDATE_INDEX}, verbosity='executionStats')
        metrics = plan['executionStats']
        assert metrics['nReturned'] == 1 and metrics['totalDocsExamined'] <= 1
        assert CANDIDATE_INDEX in str(plan['queryPlanner'])
        vector = search(c, 'sentinel', method='vector')
        assert vector['total'] == 0 and vector['branch_status']['metadata_dense'] == 'BLOCKED_SCALE'
        evidence = os.environ.get('ETL_METADATA_NEXT_EVIDENCE')
        if evidence:
            Path(evidence, f'scale-{count}.json').write_text(json.dumps({
                'scope': 'physical isolated PG16/Mongo7; synthetic metadata-only; no real HTTP/model',
                'eligible_resources': count, 'qrels_status': 'SYNTHETIC_EXACT_SENTINEL_ASSERTION_NOT_HUMAN_QRELS',
                'samples': 5, 'ids': ids, 'candidate_recall_exact_sentinel': 1., 'resource_recall_exact_sentinel': 1.,
                'latency_seconds': durations, 'cold_first_seconds': durations[0],
                'warm_p50_seconds': sorted(durations[1:])[1], 'warm_p95_seconds': max(durations[1:]),
                'index_seconds': index_seconds, 'sql_statements': statements,
                'candidate_index_execution': {'docs_examined': metrics['totalDocsExamined'],
                    'keys_examined': metrics['totalKeysExamined'], 'returned': metrics['nReturned']},
                'cpu_seconds': duration_cpu, 'dense_status': 'BLOCKED_SCALE',
                'authority_validation': 'bounded full scope keyset pages; linear cost NOT sublinear ANN',
                'index_bytes': database.command('collStats', service.store.collection.name).get('totalIndexSize')}, indent=2), encoding='utf-8')
    event.remove(c.engine, 'before_cursor_execute', before)


@pytest.mark.parametrize('count', [2000, 10000])
def test_paired_redundant_empty_content_validation_cost(metadata, mongo_test_database, count, monkeypatch):
    """Restore only the old redundant read, then compare on the SAME DB/index."""
    c, _, _, service = metadata
    # Controlled measurement only; the earlier15s run is retained as a timeout
    # failure. Never change the application's15s default to obtain a PASS.
    c.settings.search_timeout_seconds = 120
    uri, name = mongo_test_database
    statements = [0]
    def counter(*_):
        statements[0] += 1
    event.listen(c.engine, 'before_cursor_execute', counter)
    try:
        with MongoClient(uri) as client:
            database = client[name]
            install(database, candidates=True)
            service.store = MongoMetadataIndex(database)
            c.settings.search_metadata_indexed_candidates_enabled = True
            seed(c, count)
            index_pages(c, service)
            original = service.final_validate
            def extra_empty_read(items, snapshots, filters, access, **kwargs):
                validated = original(items, snapshots, filters, access, **kwargs)
                if not kwargs.get('content'):
                    original([], snapshots, filters, access, content=True)
                return validated
            measurements, signatures = {}, {}
            for phase in ('before', 'after'):
                monkeypatch.setattr(service, 'final_validate', extra_empty_read if phase == 'before' else original)
                times, reads = [], []
                cpu = process_time()
                for _ in range(5):
                    started, initial = perf_counter(), statements[0]
                    response = search(c, 'sentinel')
                    times.append(perf_counter() - started)
                    reads.append(statements[0] - initial)
                    signatures[phase] = response
                    assert response['total'] == 1
                measurements[phase] = {'samples': 5, 'seconds': times, 'sql_statements': reads,
                    'warm_p50_seconds': sorted(times[1:])[1], 'warm_p95_seconds': max(times[1:]),
                    'cpu_seconds': process_time() - cpu}
            assert signatures['before'] == signatures['after']
            assert all(after * 3 == before * 2 for before, after in zip(
                measurements['before']['sql_statements'], measurements['after']['sql_statements'], strict=True))
            evidence = os.environ.get('ETL_METADATA_NEXT_EVIDENCE')
            if evidence:
                try:
                    import resource
                except ImportError:
                    peak = None
                else:
                    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
                Path(evidence, f'paired-{count}.json').write_text(json.dumps({
                    'scope': 'physical PG/Mongo synthetic, no HTTP/model; old empty-read restored in test only',
                    'same_corpus_and_index': True, 'resources': count, 'response_parity': True,
                    'fixture_measurement_budget_seconds': 120, 'runtime_default_seconds_unchanged': 15,
                    'response_fingerprint': digest(signatures['after']),
                    'peak_rss_bytes': peak,
                    'measurements': measurements}, indent=2), encoding='utf-8')
    finally:
        event.remove(c.engine, 'before_cursor_execute', counter)
