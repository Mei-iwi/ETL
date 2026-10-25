"""Real offline BGE-M3 on public development fixtures; no draft-term approval.

Every vector/hybrid simulated request encodes once. No global vector/result
cache, model/template changes, production database or fake authenticated claim.
"""
import argparse
import json
import os
from pathlib import Path
from time import perf_counter

from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.evaluation import checksum, percentile
from data_sync_etl.domain.lexical import analysis_scope, bm25, lexical_document
from data_sync_etl.domain.retrieval_fusion import vector_priority_rrf, version_rrf
from data_sync_etl.domain.search import rrf
from scripts.benchmark_hybrid_snapshot import peak_memory_bytes, quality, summary


def run(args):
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import numpy as np
    import torch

    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding

    if args.output.exists() or not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('New private output required')
    extra = json.loads(args.queries.read_text(encoding='utf-8'))
    base = json.loads(Path(extra['base_fixture']).read_text(encoding='utf-8'))
    queries = base['queries'] + extra['queries']
    if len(queries) > 100 or len(base['documents']) > 100:
        raise ValueError('Bounded public fixture required')
    dictionary = default_dictionary()
    fingerprint = dictionary.fingerprint
    documents = {d['id']: lexical_document([d['text']], d['title']) for d in base['documents']}
    keys, versions = list(documents), {key: key for key in documents}
    revision = (args.model / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    torch.set_num_threads(2)
    model = LocalSentenceEmbedding(args.model, 'BAAI/bge-m3', revision, 1024)
    t = perf_counter()
    model.prepare()
    load_seconds = perf_counter() - t
    vectors = np.asarray([model.embed('Title: ' + d['title'] + '\nContent: ' + d['text'])
                          for d in base['documents']], dtype=np.float64)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    model.embed('warmup')
    bm25('warmup', documents)
    query_encodes, rows = 0, []
    for query in queries:
        for method in ('bow', 'vector', 'hybrid', 'weighted_050', 'vector_priority'):
            times, stages = [], []
            for _ in range(args.repeats):
                start = perf_counter()
                t = perf_counter()
                with analysis_scope():
                    lexical = bm25(query['text'], documents, dictionary=dictionary, domain=query.get('domain')) if method != 'vector' else {}
                lexical_ms = (perf_counter() - t) * 1000
                dense, encoding_ms, dense_ms = {}, 0., 0.
                if method != 'bow':
                    t = perf_counter()
                    vector = np.asarray(model.embed(query['text']), dtype=np.float64)
                    query_encodes += 1
                    vector /= np.linalg.norm(vector)
                    encoding_ms = (perf_counter() - t) * 1000
                    t = perf_counter()
                    dense = {key: float(score) for key, score in zip(keys, vectors @ vector, strict=True) if score > 0.}
                    dense_ms = (perf_counter() - t) * 1000
                t = perf_counter()
                if method == 'weighted_050':
                    scores = version_rrf({'bow': lexical, 'vector': dense}, versions,
                                         weights={'bow': .5, 'vector': 1.}).scores
                elif method == 'vector_priority':
                    scores = vector_priority_rrf(query['text'], {'bow': lexical, 'vector': dense}, versions).scores
                else:
                    scores = rrf(lexical, dense) if method == 'hybrid' else dense if method == 'vector' else lexical
                order = sorted(scores, key=lambda k: (-scores[k], k))
                fusion_ms = (perf_counter() - t) * 1000
                times.append((perf_counter() - start) * 1000)
                stages.append({'lexical_ms': lexical_ms, 'encoding_ms': encoding_ms,
                               'dense_ms': dense_ms, 'fusion_ms': fusion_ms})
            rows.append({'query_id': query['id'], 'language': query['language'], 'category': query['kind'],
                'method': method, 'resource': quality(order, query['qrels']), 'chunk': None, 'snippet': None,
                'ranking_ms': times, 'stages': stages,
                'top_k': [{'resource_id': key, 'score': scores[key]} for key in order[:10]]})
        print(json.dumps({'completed_query': query['id']}), flush=True)
    assert dictionary.fingerprint == fingerprint
    methods = sorted({r['method'] for r in rows})
    profile = {method: {stage: {'p50': percentile([s[stage] for r in rows if r['method'] == method for s in r['stages']], .5),
                              'p95': percentile([s[stage] for r in rows if r['method'] == method for s in r['stages']], .95)}
                        for stage in ('lexical_ms', 'encoding_ms', 'dense_ms', 'fusion_ms')} for method in methods}
    result = {'status': extra['label_status'], 'base_fixture_checksum': checksum(base), 'query_fixture_checksum': checksum(extra),
        'model': {'model_id': model.model_id, 'revision': revision, 'dimension': model.dimension, 'engine': 'SentenceTransformer'},
        'model_loads': model._load_count, 'model_load_seconds': load_seconds, 'threads': 2,
        'query_encodes': query_encodes, 'expected_query_encodes': len(queries) * 4 * args.repeats,
        'queries': len(queries), 'repeats': args.repeats, 'dictionary_fingerprint': fingerprint,
        'draft_terminology_used': False, 'scope': 'OFFLINE_PUBLIC_FIXTURE_REAL_MODEL_NO_HTTP_OR_DB',
        'peak_process_rss_bytes': peak_memory_bytes(), 'profile': profile,
        'summary': {m: summary([r for r in rows if r['method'] == m]) for m in methods},
        'by_language': {m: {lang: summary([r for r in rows if r['method'] == m and r['language'] == lang])
                           for lang in sorted({r['language'] for r in rows})} for m in methods},
        'by_category': {m: {category: summary([r for r in rows if r['method'] == m and r['category'] == category])
                           for category in sorted({r['category'] for r in rows})} for m in methods}, 'rows': rows}
    assert result['model_loads'] == 1 and query_encodes == result['expected_query_encodes']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'queries': len(queries), 'model_loads': result['model_loads']}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queries', type=Path, default=Path('fixtures/search/phase2_queries.json'))
    p.add_argument('--model', type=Path, default=Path('Models/bge-m3'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repeats', type=int, choices=(1, 2, 3), default=2)
    run(p.parse_args())


if __name__ == '__main__':
    main()
