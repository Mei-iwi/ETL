"""Offline paired baseline/BM25/Hybrid benchmark, never connects to databases."""

import argparse
import json
import math
import os
from pathlib import Path
from statistics import mean, median
from time import perf_counter

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.keywords import UnigramKeywords
from data_sync_etl.domain.lexical import (
    ANALYZER_VERSION,
    REPRESENTATION_VERSION,
    bm25,
    lexical_document,
)
from data_sync_etl.domain.search import binary_score, cosine, rrf


def metrics(ranked, qrels, k):
    relevant = {key for key, grade in qrels.items() if grade > 0}
    recall = len(set(ranked[:k]) & relevant) / len(relevant) if relevant else 0.0
    dcg = sum((2 ** qrels.get(key, 0) - 1) / math.log2(i + 2)
              for i, key in enumerate(ranked[:k]))
    ideal = sum((2 ** grade - 1) / math.log2(i + 2)
                for i, grade in enumerate(sorted(qrels.values(), reverse=True)[:k]))
    reciprocal = next((1 / (i + 1) for i, key in enumerate(ranked) if key in relevant), 0.0)
    return {'recall': recall, 'ndcg': dcg / ideal if ideal else 0.0, 'mrr': reciprocal}


def benchmark(args):
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch

    torch.set_num_threads(2)
    corpus = json.loads(args.corpus.read_text(encoding='utf-8'))
    documents = corpus['documents']
    extractor = UnigramKeywords()
    keywords = {doc['id']: extractor.extract(doc['text']) for doc in documents}
    lexical = {doc['id']: lexical_document([doc['text']], doc['title']) for doc in documents}
    model = LocalSentenceEmbedding(args.model, 'BAAI/bge-m3',
        (args.model / 'etl-model-revision.txt').read_text(encoding='utf-8').strip(), 1024)
    started = perf_counter()
    model.prepare()
    startup = perf_counter() - started
    # Match production's EXISTING keyword-input vector representation. Contextual
    # dense embedding is deliberately not claimed or silently substituted here.
    vectors = {key: model.embed(' | '.join(terms)) for key, terms in keywords.items()}
    assert all(len(vector) == 1024 for vector in vectors.values())
    rows = []
    for query in corpus['queries']:
        branches = {}
        for method in ('baseline', 'bm25', 'hybrid'):
            times = []
            for _ in range(args.repeats):
                started = perf_counter()
                if method == 'baseline':
                    query_keywords = extractor.extract(query['text'])
                    scores = {key: binary_score(query_keywords, terms)[0]
                              for key, terms in keywords.items()}
                    scores = {key: score for key, score in scores.items() if score > 0}
                else:
                    scores = bm25(query['text'], lexical)
                    if method == 'hybrid':
                        vector = model.embed(query['text'])
                        dense = {key: cosine(vector, value) for key, value in vectors.items()}
                        scores = rrf(scores, {key: score for key, score in dense.items()
                                              if score >= args.vector_min_similarity})
                ranked = sorted(scores, key=lambda key: (-scores[key], key))
                times.append((perf_counter() - started) * 1000)
            branches[method] = {**metrics(ranked, query['qrels'], args.k),
                                'latency_ms': median(times), 'top_k': ranked[:args.k]}
        rows.append({'query_id': query['id'], 'kind': query['kind'], 'methods': branches})
        print(query['id'], flush=True)
    summary = {method: {metric: mean(row['methods'][method][metric] for row in rows)
                        for metric in ('recall', 'ndcg', 'mrr', 'latency_ms')}
               for method in ('baseline', 'bm25', 'hybrid')}
    regressions = {f'{method}_vs_{base}': [row['query_id'] for row in rows
                    if row['methods'][method]['ndcg'] < row['methods'][base]['ndcg'] - 1e-9]
                   for method, base in [('bm25', 'baseline'), ('hybrid', 'bm25')]}
    return {'k': args.k, 'documents': len(documents), 'queries': len(rows),
            'corpus_sha256': digest(corpus), 'analyzer': ANALYZER_VERSION,
            'lexical_representation': REPRESENTATION_VERSION,
            'repeats': args.repeats, 'model': model.model_id, 'revision': model.version,
            'actual_dimension': len(next(iter(vectors.values()))), 'startup_seconds': startup,
            'dense_input': 'existing production keyword-joined representation',
            'vector_min_similarity': args.vector_min_similarity,
            'scope': 'offline ranking/encoding only; excludes DB, auth, snapshot verification, HTTP and cache',
            'limitations': ['Small hand-labelled development corpus; no generalization claim.',
                            'Dense retains top5 keyword limitations; contextual dense was not implemented.'],
            'summary': summary, 'ndcg_regressions': regressions, 'per_query': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=Path('fixtures/search/lexical_benchmark.json'))
    parser.add_argument('--model', type=Path, default=Path('Models/bge-m3'))
    parser.add_argument('--output', type=Path, default=Path('storage/benchmarks/lexical.json'))
    parser.add_argument('--k', type=int, default=5)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--vector-min-similarity', type=float, default=0.6)
    args = parser.parse_args()
    if not 1 <= args.k <= 100 or not 1 <= args.repeats <= 10:
        parser.error('k must be 1..100; repeats must be 1..10')
    if not 0 < args.vector_min_similarity <= 1:
        parser.error('vector-min-similarity must be > 0 and <= 1')
    result = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
