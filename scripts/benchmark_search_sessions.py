"""Bounded synthetic BM25/gateway comparison; no DB, HTTP or real BGE model.

Prints measurements without writing reports. Fixture rights and revision fences
are NOT evidence of production authorization. Corpus/analyzer warmup is excluded.
"""
import json
import statistics
import tracemalloc
from time import perf_counter, process_time

from data_sync_etl.application.search_session_gateway import SearchSessionGateway
from data_sync_etl.application.search_sessions import RankedHit
from data_sync_etl.domain.lexical import analysis_scope, bm25, lexical_document
from data_sync_etl.domain.search_access import SearchAccess


def summary(samples):
    ordered = sorted(samples)
    return {'samples': len(ordered), 'p50_ms': statistics.median(ordered),
            'p95_ms': ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]}


def run(doc_count=50):
    if type(doc_count) is not int or not 20 <= doc_count <= 100:
        raise ValueError('Synthetic corpus must contain 20..100 documents')
    with analysis_scope():
        documents = {f'r{i:03}': lexical_document(['Synthetic topic learning content ' * (1 + i % 3)])
                     for i in range(doc_count)}
    actors = [SearchAccess('authenticated_user', actor_id=name) for name in ('alice', 'bob')]
    criteria = {'query': 'synthetic topic', 'method': 'bow', 'filters': {}, 'size': 5}
    counts = {'rank': 0, 'hydrate': 0}

    def rank(criteria, access):
        counts['rank'] += 1
        # Fixture public/owner partition, not a live ACL adapter.
        eligible = {key: doc for i, (key, doc) in enumerate(documents.items())
                    if access.actor_id == 'alice' or i % 2 == 0}
        scores = bm25(criteria['query'], eligible, expand_concepts=False)
        return tuple(RankedHit(key, 'v' + key, 'b' + key, 'u' + key, score)
                     for key, score in sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))), False

    def hydrate(refs, criteria, access):
        counts['hydrate'] += 1
        return [dict(resource_id=ref.resource_id, resource_version_id=ref.version_id, bag_id=ref.bag_id,
            content_unit_id=ref.unit_id, score=ref.score, snippet='current synthetic topic evidence') for ref in refs]

    def measure(cached):
        counts.update(rank=0, hydrate=0)
        gateway = SearchSessionGateway(lambda _: 'fixture-r1', rank, hydrate, lambda *_: {'message': 'OK'})
        samples, evidence = [], []
        tracemalloc.start()
        cpu_start = process_time()
        for actor in actors:
            token = None
            for page in (1, 2, 1, 3, 4):
                started = perf_counter()
                if cached:
                    result = gateway.read(criteria, actor, page=page, size=5, token=token)
                    token = result['search_session']['id']
                    rows = result['items']
                else:
                    refs, _ = rank(criteria, actor)
                    rows = hydrate(refs[(page - 1) * 5:page * 5], criteria, actor)
                samples.append((perf_counter() - started) * 1000)
                evidence.append(rows)
        cpu = process_time() - cpu_start
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        return {'latency': summary(samples), 'calls': dict(counts), 'cpu_seconds': cpu,
                'peak_python_allocated_bytes': peak, 'session_accounted_bytes': gateway.sessions._bytes}, evidence

    before, original = measure(False)
    after, reused = measure(True)
    if original != reused:
        raise AssertionError('Rank/score/evidence changed between paths')
    return {'scope': 'synthetic_bm25_gateway_not_http', 'documents': doc_count, 'actors': 2,
            'navigation': [1, 2, 1, 3, 4], 'warmup_excluded': True, 'evidence_identical': True,
            'independent_qrels': False, 'bge_m3': 'NOT_MEASURED', 'rss': 'NOT_MEASURED',
            'before': before, 'after': after}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
