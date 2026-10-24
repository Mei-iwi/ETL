"""Paired offline retrieval replay. No Container, DB, credentials or runtime writes.

Uses an existing frozen corpus and identity-bound BGE-M3 artifacts. Timings are
warm ranking only: never HTTP, encoding, database or authenticated acceptance.
All output remains private; publish aggregate metrics separately.
"""
import argparse
import json
import platform
import sys
from collections import defaultdict
from pathlib import Path
from time import perf_counter

import numpy as np

from data_sync_etl.application.vector_quality import compile_qrels, evaluate_ranking
from data_sync_etl.domain.evaluation import checksum, percentile
from data_sync_etl.domain.lexical import analysis_scope, bm25, lexical_document, surface_tokens
from data_sync_etl.domain.search import binary_score, normalize_query, rrf
from scripts.optimize_semantic_quality import check_vectors, inputs


def peak_memory_bytes():
    if sys.platform == 'win32':
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD),
                        *[(name, ctypes.c_size_t) for name in ('peak_working_set', 'working_set',
                          'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        memory = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
        memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not memory(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise OSError('Cannot sample benchmark process memory')
        return counters.peak_working_set
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def quality(order, qrels):
    result = evaluate_ranking(order, qrels)
    positive = {key for key, grade in qrels.items() if grade >= 2}
    result['recall10'] = len(set(order[:10]) & positive) / len(positive) if positive else None
    result['candidate_recall'] = len(set(order) & positive) / len(positive) if positive else None
    return result


def summary(rows):
    positive = [r for r in rows if r['resource']['has_positive']]
    negative = [r for r in rows if not r['resource']['has_positive']]
    output = {'queries': len(rows), 'positive_queries': len(positive), 'no_answer_queries': len(negative)}
    for level in ('resource', 'chunk', 'snippet'):
        output[level] = {}
        for metric in ('precision1', 'precision5', 'recall5', 'recall10', 'mrr10', 'ndcg10',
                       'hit1', 'hit5', 'candidate_recall'):
            values = [r[level][metric] for r in positive if r[level] is not None and r[level][metric] is not None]
            output[level][metric] = sum(values) / len(values) if values else None
    output['no_answer_fpr'] = (sum(not r['resource']['abstained'] for r in negative) / len(negative)
                               if negative else None)
    output['false_abstention_rate'] = (sum(r['resource']['abstained'] for r in positive) / len(positive)
                                       if positive else None)
    times = [t for r in rows for t in r['ranking_ms']]
    output['ranking_latency_ms'] = {'p50': percentile(times, .5), 'p95': percentile(times, .95),
                                    'samples': len(times)}
    output['serial_ranking_queries_per_second'] = len(times) * 1000 / sum(times) if sum(times) else None
    return output


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def run(args):
    if (not args.output.resolve().is_relative_to(Path('.test-tmp').resolve())
            or args.output.exists() or args.corpus.stat().st_size > 64 * 1024 * 1024):
        raise ValueError('New bounded private output below .test-tmp required')
    corpus, specification, prepared = load(args.corpus), load(args.spec), load(args.prepared)
    if checksum({k: v for k, v in corpus.items() if k != 'checksum'}) != corpus['checksum']:
        raise ValueError('Corpus checksum mismatch')
    qrels = compile_qrels(corpus, specification)
    freeze = load(args.spec.with_name('freeze.json'))
    if (freeze['corpus_checksum'] != corpus['checksum']
            or freeze['specification_checksum'] != checksum(specification)
            or freeze['qrels_checksum'] != checksum(qrels)):
        raise ValueError('Frozen qrels identity mismatch')
    if (prepared['corpus_checksum'] != corpus['checksum']
            or prepared['specification_checksum'] != qrels['specification_checksum']
            or prepared['model']['model_id'] != 'BAAI/bge-m3'
            or prepared['model']['dimension'] != 1024):
        raise ValueError('Frozen model/corpus/query identity mismatch')
    if len(corpus['resources']) > 100 or len(qrels['queries']) > 100:
        raise ValueError('Bounded corpus required')
    contexts, locators = inputs(corpus, 'verified_full_context')
    keys = list(locators)
    if len(keys) > 2000:
        raise ValueError('Bounded chunks required')
    queries = [normalize_query(q['query']) for q in qrels['queries']]
    artifacts, matrices = {}, {}
    for name, texts, artifact_name in (
        ('contextual', contexts, 'verified_full_context'),
        ('raw', inputs(corpus, 'raw_content')[0], 'raw_content'),
        ('query', queries, 'new_queries'),
    ):
        artifact = prepared['artifacts'][artifact_name]
        tag = {'contextual': 'contextual', 'raw': 'raw', 'query': 'optimization_queries'}[name]
        identity = checksum([corpus['checksum'], prepared['model'], texts, tag])
        if identity != artifact['identity'] or identity[:16] not in Path(artifact['path']).name:
            raise ValueError('Artifact input/order identity mismatch')
        matrices[name] = check_vectors(Path(artifact['path']), len(texts))
        artifacts[name] = {'input_identity': identity, 'bytes': Path(artifact['path']).stat().st_size,
                           'file_sha256': __import__('hashlib').sha256(Path(artifact['path']).read_bytes()).hexdigest()}
    bags, bag_chunks, lexical, resources, versions = {}, defaultdict(list), {}, {}, {}
    for resource in corpus['resources']:
        for bag in resource['bags']:
            bags[bag['id']] = bag
            resources[bag['id']] = resource['resource_id']
            versions[bag['id']] = resource['version_id']
    for index, key in enumerate(keys):
        chunk = locators[key]
        if (chunk['retrieval_eligible'] and chunk['review_status'] == 'APPROVED'
                or args.include_unreviewed_for_offline_analysis):
            bag_chunks[chunk['bag_id']].append(index)
    # No use of denied text/vectors, even in the offline ranking corpus.
    bags = {key: value for key, value in bags.items() if key in bag_chunks}
    if not bags:
        raise ValueError('Snapshot has no eligible approved chunks; live-quality baseline is BLOCKED')
    for bag in bags:
        lexical[bag] = lexical_document([locators[keys[i]]['text'] for i in bag_chunks[bag]],
                                       locators[keys[bag_chunks[bag][0]]]['metadata']['title']
                                       if 'metadata' in locators[keys[bag_chunks[bag][0]]] else None)
    # Replay export's legacy keyword-bag vectors as a separately labelled arm.
    bag_keys = list(bags)
    bag_matrix = np.asarray([bags[key]['vector'] for key in bag_keys], dtype=np.float64)
    if bag_matrix.shape != (len(bag_keys), 1024) or not np.isfinite(bag_matrix).all():
        raise ValueError('Legacy bag vector contract mismatch')
    norms = np.linalg.norm(bag_matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValueError('Zero legacy vector')
    bag_matrix /= norms
    rows = []
    methods = ['binary_legacy', 'bow', 'vector_legacy', 'vector_raw', 'vector', 'hybrid']
    if args.version_fusion:
        methods.append('hybrid_version')
    # Separate one-time analyzer initialization from warm ranking measurements.
    bm25('warmup', lexical)
    for qi, query in enumerate(qrels['queries']):
        for method in methods:
            times = []
            for _ in range(args.repeats):
                started = perf_counter()
                with analysis_scope():
                    bow = (bm25(query['query'], lexical) if method in ('bow', 'hybrid', 'hybrid_version')
                           else {key: binary_score(surface_tokens(query['query']),
                                                  surface_tokens(bags[key]['embedding_text']))[0]
                                 for key in bags} if method == 'binary_legacy' else {})
                bow = {key: score for key, score in bow.items() if score > 0}
                dense, chosen, chunk_scores = {}, {}, {}
                if method not in ('bow', 'binary_legacy'):
                    values = (bag_matrix @ matrices['query'][qi] if method == 'vector_legacy'
                              else matrices['raw' if method == 'vector_raw' else 'contextual'] @ matrices['query'][qi])
                    for bi, bag in enumerate(bag_keys):
                        indices = bag_chunks[bag]
                        best = min(indices, key=lambda i: (-float(values[i]), keys[i])) if method != 'vector_legacy' else indices[0]
                        score = float(values[bi] if method == 'vector_legacy' else values[best])
                        chosen[bag] = keys[best]
                        if score > args.dense_threshold:
                            dense[bag] = score
                        if method != 'vector_legacy':
                            chunk_scores.update({keys[i]: float(values[i]) for i in indices if values[i] > args.dense_threshold})
                if method == 'hybrid_version':
                    from data_sync_etl.domain.retrieval_fusion import version_rrf
                    fused = version_rrf({'bow': bow, 'vector': dense}, versions)
                    scores = fused.scores
                    representatives = fused.representatives
                else:
                    scores = rrf(bow, dense) if method == 'hybrid' else bow if method in ('bow', 'binary_legacy') else dense
                    representatives = {}
                order = sorted(scores, key=lambda key: (-scores[key],
                    key != representatives.get(versions[key], key), key))
                grouped, snippets = [], []
                for bag in order:
                    if resources[bag] in grouped:
                        continue
                    grouped.append(resources[bag])
                    snippets.append(chosen.get(bag, keys[bag_chunks[bag][0]]))
                times.append((perf_counter() - started) * 1000)
            rows.append({'query_id': query['query_id'], 'language': query['language'],
                'category': query['category'], 'split': query['split'], 'method': method,
                'resource': quality(grouped, query['resource_qrels']),
                'chunk': (quality(sorted(chunk_scores, key=lambda k: (-chunk_scores[k], k)), query['chunk_qrels'])
                          if method not in ('bow', 'binary_legacy', 'vector_legacy') else None),
                'snippet': quality(snippets, query['chunk_qrels']) if method not in ('bow', 'binary_legacy', 'vector_legacy') else None,
                'ranking_ms': times,
                'top_k': [{'resource_id': key, 'chunk_id': chunk} for key, chunk in zip(grouped[:10], snippets[:10], strict=True)],
                'bag_top_k': [{'id': key, 'score': scores[key]} for key in order[:10]]})
        print(json.dumps({'completed_query': qi + 1, 'total': len(qrels['queries'])}), flush=True)
    report = {'status': 'EXPLORATORY_DRAFT_NOT_ACCEPTANCE', 'source_revision': args.source_revision,
        'corpus_checksum': corpus['checksum'], 'qrels_checksum': checksum(qrels),
        'qrels_status': qrels['label_status'], 'model': prepared['model'], 'artifacts': artifacts,
        'resources': len(corpus['resources']), 'bags': len(bags), 'chunks': len(keys),
        'eligible_chunks': sum(map(len, bag_chunks.values())), 'queries': len(qrels['queries']),
        'include_unreviewed_for_offline_analysis': args.include_unreviewed_for_offline_analysis,
        'scope': 'offline frozen replay; warm ranking only; no HTTP/auth/DB/encoding/production Meaning Bag claim',
        'dense_threshold': args.dense_threshold, 'runtime': platform.python_version(),
        'peak_process_rss_bytes': peak_memory_bytes(),
        'limitations': ['BoW/legacy bag chunk and snippet metrics are NOT_RUN (null), not zero.',
                         'Hybrid chunk metrics describe dense pre-fusion candidates, not a fused chunk ranking.',
                         'Dense snippet metrics describe chosen max-cosine passage, not production ContextRanker.',
                         'Lexical representations are reconstructed content-only because export omits per-bag titles/TF.',
                         'Corpus is an older frozen export, not current live index coverage.',
                         'Cached FlagEmbedding vectors, not a fresh model inference benchmark.',
                         'Sparse/ColBERT are NOT_RUN; no no-answer calibration or holdout tuning.'],
        'summary': {m: summary([r for r in rows if r['method'] == m]) for m in methods},
        'by_language': {m: {lang: summary([r for r in rows if r['method'] == m and r['language'] == lang])
                           for lang in sorted({r['language'] for r in rows})} for m in methods},
        'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'summary': report['summary']}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--spec', type=Path, required=True)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--source-revision', required=True)
    p.add_argument('--repeats', type=int, choices=range(1, 31), default=5)
    p.add_argument('--dense-threshold', type=float, default=0.)
    p.add_argument('--version-fusion', action='store_true')
    p.add_argument('--include-unreviewed-for-offline-analysis', action='store_true',
                   help='Offline exported text only; never implies production eligibility or authenticated acceptance')
    args = p.parse_args()
    if not 0 <= args.dense_threshold < 1:
        p.error('dense-threshold must be 0..1 exclusive')
    run(args)


if __name__ == '__main__':
    main()
