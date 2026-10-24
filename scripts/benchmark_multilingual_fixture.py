"""Explicit synthetic development ablation; draft terms NEVER enter runtime.

No DB reads. BGE-M3 is offline and resident. Outputs contain only public fixture
IDs and metrics. Research dictionaries are constructed in memory for this tool.
"""
import argparse
import json
import os
import platform
from pathlib import Path
from time import perf_counter

from data_sync_etl.domain.concepts import Dictionary, Snapshot, normalize
from data_sync_etl.domain.evaluation import checksum, percentile
from data_sync_etl.domain.lexical import analysis_scope, bm25, lexical_document
from data_sync_etl.domain.search import rrf
from scripts.benchmark_hybrid_snapshot import peak_memory_bytes, quality, summary


def fixture_snapshot(fixture, *, research_approved=False):
    status = 'approved' if research_approved else 'pending'
    source = [{'name': 'Authored synthetic development fixture; independent review pending',
               'identifier': fixture['version']}]
    concepts = []
    for row in fixture['concepts']:
        cid = 'fixture:' + row['id']
        terms = [{'text': text, 'normalized': normalize(text),
                  'language': lang, 'kind': kind, 'concept_id': cid, 'domain': row['domain'],
                  'context_hints': row['hints'], 'sources': source, 'confidence': 1., 'review_status': status}
                 for text, lang, kind in row['terms']]
        concepts.append({'concept_id': cid,
            'preferred_labels': {lang: text for text, lang, kind in row['terms'] if kind == 'preferred'},
            'definition': 'Synthetic lexical equivalence fixture, not a reviewed production glossary.',
            'domain': row['domain'], 'terms': terms, 'relations': [], 'sources': source,
            'review_status': status, 'version': '1'})
    return Snapshot.model_validate({'version': fixture['version'], 'concepts': concepts})


def run(args):
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import numpy as np
    import torch

    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding

    if args.output.exists() or not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('New private output required')
    fixture = json.loads(args.fixture.read_text(encoding='utf-8'))
    dictionary = Dictionary(fixture_snapshot(fixture, research_approved=True))
    documents = {d['id']: lexical_document([d['text']], d['title']) for d in fixture['documents']}
    keys = list(documents)
    torch.set_num_threads(2)
    revision = (args.model / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    model = LocalSentenceEmbedding(args.model, 'BAAI/bge-m3', revision, 1024)
    started = perf_counter()
    model.prepare()
    load_seconds = perf_counter() - started
    started = perf_counter()
    vectors = np.asarray([model.embed('Title: ' + d['title'] + '\nContent: ' + d['text'])
                          for d in fixture['documents']], dtype=np.float64)
    vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
    index_seconds = perf_counter() - started
    # Warm encoder and analyzer before repeated request measurements.
    model.embed('warmup')
    bm25('warmup', documents)
    rows, timings = [], {}
    for query in fixture['queries']:
        for method in ('bow', 'bow_draft_terms', 'vector', 'hybrid', 'hybrid_draft_terms'):
            times, stages = [], []
            for _ in range(args.repeats):
                start = perf_counter()
                with analysis_scope():
                    bow = bm25(query['text'], documents, domain=query.get('domain'),
                        dictionary=dictionary if method.endswith('draft_terms') else None) if method != 'vector' else {}
                lexical_ms = (perf_counter() - start) * 1000
                encoding_ms, dense_ms, dense = 0., 0., {}
                if method in ('vector', 'hybrid', 'hybrid_draft_terms'):
                    encode_start = perf_counter()
                    vector = np.asarray(model.embed(query['text']), dtype=np.float64)
                    vector /= np.linalg.norm(vector)
                    encoding_ms = (perf_counter() - encode_start) * 1000
                    dense_start = perf_counter()
                    dense = {key: float(score) for key, score in zip(keys, vectors @ vector, strict=True)
                             if score > 0.}
                    dense_ms = (perf_counter() - dense_start) * 1000
                fusion_start = perf_counter()
                scores = rrf(bow, dense) if method.startswith('hybrid') else dense if method == 'vector' else bow
                order = sorted(scores, key=lambda k: (-scores[k], k))
                fusion_ms = (perf_counter() - fusion_start) * 1000
                times.append((perf_counter() - start) * 1000)
                stages.append({'lexical_ms': lexical_ms, 'encoding_ms': encoding_ms,
                               'dense_ms': dense_ms, 'fusion_sort_ms': fusion_ms})
            metrics = quality(order, query['qrels'])
            rows.append({'query_id': query['id'], 'language': query['language'], 'category': query['kind'],
                         'method': method, 'resource': metrics, 'chunk': metrics, 'snippet': metrics,
                         'ranking_ms': times, 'stages': stages,
                         'top_k': [{'resource_id': key, 'score': scores[key]} for key in order[:10]]})
        print(json.dumps({'completed_query': query['id']}), flush=True)
    methods = sorted({r['method'] for r in rows})
    for method in methods:
        timings[method] = {stage: {'p50': percentile([s[stage] for r in rows if r['method'] == method for s in r['stages']], .5),
                                  'p95': percentile([s[stage] for r in rows if r['method'] == method for s in r['stages']], .95)}
                           for stage in ('lexical_ms', 'encoding_ms', 'dense_ms', 'fusion_sort_ms')}
    report = {'status': fixture['label_status'], 'fixture_sha256': checksum(fixture), 'runtime': platform.python_version(),
        'model': {'id': model.model_id, 'revision': revision, 'dimension': 1024, 'engine': 'SentenceTransformer'},
        'threads': 2, 'documents': len(documents), 'queries': len(fixture['queries']), 'repeats': args.repeats,
        'scope': 'offline synthetic end-to-end lexical/query-encode/exact-cosine/fusion; no HTTP/DB/auth; cache OFF',
        'model_load_seconds': load_seconds, 'document_encoding_seconds': index_seconds,
        'index_vector_bytes': vectors.nbytes, 'draft_terms_runtime_enabled': False,
        'peak_process_rss_bytes': peak_memory_bytes(),
        'levels_note': 'Each synthetic document is one passage; resource/chunk/snippet IDs coincide here only.',
        'summary': {m: summary([r for r in rows if r['method'] == m]) for m in methods},
        'by_language': {m: {lang: summary([r for r in rows if r['method'] == m and r['language'] == lang])
                           for lang in sorted({r['language'] for r in rows})} for m in methods},
        'profile': timings, 'rows': rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'summary': report['summary']}), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture', type=Path, default=Path('fixtures/search/multilingual_context.json'))
    p.add_argument('--model', type=Path, default=Path('Models/bge-m3'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repeats', type=int, choices=range(1, 6), default=2)
    run(p.parse_args())


if __name__ == '__main__':
    main()
