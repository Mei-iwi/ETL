"""Paired BM25 / equivalent expansion / contextual expansion; no DB or encoder."""
import argparse
import json
import math
from pathlib import Path
from statistics import mean
from time import perf_counter

from data_sync_etl.domain.concepts import default_dictionary
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import ANALYZER_VERSION, RANKING_VERSION, bm25, lexical_document


def metrics(ranked, qrels, k):
    relevant = {key for key, grade in qrels.items() if grade > 0}
    hits = len(set(ranked[:k]) & relevant)
    dcg = sum((2 ** qrels.get(key, 0) - 1) / math.log2(i + 2) for i, key in enumerate(ranked[:k]))
    ideal = sum((2 ** grade - 1) / math.log2(i + 2)
                for i, grade in enumerate(sorted(qrels.values(), reverse=True)[:k]))
    return {'precision': hits / k, 'recall': hits / len(relevant) if relevant else 0.0,
            'ndcg': dcg / ideal if ideal else 0.0,
            'negative_correct': not ranked if not relevant else None}


def percentile(values, quantile):
    return sorted(values)[max(0, math.ceil(quantile * len(values)) - 1)]


def benchmark(corpus, *, k=5, repeats=5):
    dictionary = default_dictionary()
    documents = {d['id']: lexical_document([d['text']], d.get('title')) for d in corpus['documents']}
    rows, times = [], {method: [] for method in ('bm25', 'equivalents', 'contextual')}
    # Warm tokenizer before latency samples; exclude one-time import/model load.
    bm25('warmup', documents)
    for query in corpus['queries']:
        branches = {}
        for method in times:
            expanded = dictionary.expand(query['text'], domain=query.get('domain'), contextual=method == 'contextual')
            for _ in range(repeats):
                started = perf_counter()
                scores = bm25(query['text'], documents, expand_concepts=method != 'bm25',
                              domain=query.get('domain'), contextual=method == 'contextual')
                ranked = sorted(scores, key=lambda key: (-scores[key], key))
                times[method].append((perf_counter() - started) * 1000)
            branches[method] = {**metrics(ranked, query['qrels'], k), 'top_k': ranked[:k],
                'expansion_count': sum(t.relation != 'original' for t in expanded.terms) if method != 'bm25' else 0}
        rows.append({'query_id': query['id'], 'kind': query['kind'], 'methods': branches})
    positive_rows = [r for r, q in zip(rows, corpus['queries'], strict=True) if any(q['qrels'].values())]
    summary = {method: {**{metric: mean(r['methods'][method][metric] for r in positive_rows)
                           for metric in ('precision', 'recall', 'ndcg')},
                        'expansion_count_mean': mean(r['methods'][method]['expansion_count'] for r in rows),
                        'latency_p50_ms': percentile(times[method], 0.5),
                        'latency_p95_ms': percentile(times[method], 0.95), 'latency_samples': len(times[method]),
                        'negative_queries_correct': sum(r['methods'][method]['negative_correct'] is True for r in rows)}
               for method in times}
    comparisons = {f'{new}_vs_{old}': {
        'improved': [r['query_id'] for r in rows if r['methods'][new]['ndcg'] > r['methods'][old]['ndcg'] + 1e-9],
        'regressed': [r['query_id'] for r in rows if r['methods'][new]['ndcg'] < r['methods'][old]['ndcg'] - 1e-9]}
        for new, old in [('equivalents', 'bm25'), ('contextual', 'equivalents')]}
    return {'k': k, 'documents': len(documents), 'queries': len(rows), 'positive_queries': len(positive_rows),
            'repeats': repeats, 'corpus_sha256': digest(corpus), 'analyzer': ANALYZER_VERSION,
            'ranking': RANKING_VERSION, 'dictionary_fingerprint': dictionary.fingerprint,
            'scope': 'Offline ranking on the same full corpus; excludes HTTP, DB, authorization and cold startup.',
            'limitations': [corpus['description'], 'Latency is repeated warm microbenchmark data, not production p95.',
                           'Macro relevance metrics exclude queries with no relevant documents; report their abstention separately.',
                           'Precision divides by k, including unfilled ranks. Default weights are initial values, not tuned.'],
            'summary': summary, 'comparisons': comparisons, 'per_query': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=Path('fixtures/search/concept_benchmark.json'))
    parser.add_argument('--output', type=Path, default=Path('storage/benchmarks/concepts.json'))
    parser.add_argument('--k', type=int, default=5)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.k <= 100 or not 1 <= args.repeats <= 100:
        parser.error('k and repeats must be 1..100')
    result = benchmark(json.loads(args.corpus.read_text(encoding='utf-8')), k=args.k, repeats=args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':
    main()
