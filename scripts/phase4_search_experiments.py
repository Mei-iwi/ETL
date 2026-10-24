"""Offline public-fixture diagnostics; never a production or human quality signoff."""
import argparse
import json
import subprocess
import unicodedata
from pathlib import Path
from time import perf_counter

from data_sync_etl.domain.concepts import ExpansionPolicy, default_dictionary
from data_sync_etl.domain.evaluation import checksum, percentile
from data_sync_etl.domain.lexical import analysis_scope, bm25, lexical_document
from data_sync_etl.domain.meaning_bags import build_meanings, match_meaning
from data_sync_etl.domain.search import cosine, normalize_query, rrf
from data_sync_etl.domain.semantic import embedding_input
from scripts.benchmark_hybrid_snapshot import quality


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold().replace('đ', 'd'))
                   if unicodedata.category(c) != 'Mn')


def suite(fixture):
    """Inherited DRAFT labels, transformed queries grouped by their original intent."""
    rows = []
    for query in fixture['queries']:
        rows.append({**query, 'form': 'original', 'family': query['id']})
        if query['language'] == 'vi' and fold(query['text']) != query['text'].casefold():
            rows.extend({**query, 'id': query['id'] + '-' + form, 'text': value,
                         'form': form, 'family': query['id']}
                        for form, value in [('accentless', fold(query['text'])),
                                            ('nfd', unicodedata.normalize('NFD', query['text'])),
                                            ('partial', ' '.join(w if i % 2 else fold(w)
                                                for i, w in enumerate(query['text'].split())))])
    return rows


def run(args):
    if args.output.exists() or not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('Fresh private output below .test-tmp required')
    import torch
    torch.set_num_threads(2)
    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
    fixture = json.loads(Path('fixtures/search/multilingual_context.json').read_text(encoding='utf-8'))
    additional = json.loads(Path('fixtures/search/phase2_queries.json').read_text(encoding='utf-8'))
    fixture['queries'] = [*fixture['queries'], *additional['queries']]
    dictionary = default_dictionary()  # Runtime approved dictionary ONLY; no draft fixture terminology.
    if args.engine == 'meaning_bag':
        model = LocalSentenceEmbedding(args.model, 'BAAI/bge-m3', args.revision, 1024)
        encode = model.embed
    else:
        from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
        model = BgeM3Embedding(args.model, args.revision, max_tokens=1024)
        def encode(text):
            return model.embed_queries([text])[0]['vector']
    model.prepare()
    if args.engine == 'semantic_chunk':
        # Legacy document/Meaning index and semantic adapter are distinct in production.
        inputs = [embedding_input({'text': d['text']}, title=d['title']) for d in fixture['documents']]
        vectors = [vector for offset in range(0, len(inputs), model.batch_size)
                   for vector in model.embed_documents(inputs[offset:offset + model.batch_size])]
        documents = {d['id']: vector['vector'] for d, vector in zip(fixture['documents'], vectors, strict=True)}
    else:
        documents = {d['id']: build_meanings([(d['id'], d['text'])], d['id'], d['title'], model, .8)
                     for d in fixture['documents']}
    lexical = {d['id']: lexical_document([d['text']], title=d['title']) for d in fixture['documents']}
    results, encodes = [], 0
    policy = ExpansionPolicy()
    for row in suite(fixture):
        original = normalize_query(row['text'])
        t = perf_counter()
        variants = []
        if args.variants:
            from data_sync_etl.domain.query_variants import approved_accent_variant
            restored = approved_accent_variant(original, dictionary, policy=policy)
            if restored is not None:
                variants = [restored]
        variant_ms = (perf_counter() - t) * 1000
        dense_queries = list(dict.fromkeys([original, *variants]))
        t = perf_counter()
        encoded = [encode(text) for text in dense_queries]
        encodes += len(encoded)
        encode_ms = (perf_counter() - t) * 1000
        t = perf_counter()
        scores, raw_scores = {}, {}
        for key, document in documents.items():
            values = [match_meaning(v, document)[0] if args.engine == 'meaning_bag' else cosine(v, document)
                      for v in encoded]
            raw_scores[key] = max(values)
            if (max(values) >= .6 if args.engine == 'meaning_bag' else max(values) > 0):
                scores[key] = max(values)
        dense_ms = (perf_counter() - t) * 1000
        t = perf_counter()
        with analysis_scope():
            bow = bm25(original, lexical, dictionary=dictionary)
            for variant in variants:
                for key, value in bm25(variant, lexical, dictionary=dictionary).items():
                    bow[key] = max(bow.get(key, 0), policy.synonyms * value)
        lexical_ms = (perf_counter() - t) * 1000
        for method, branch in [('bow', bow), ('vector', scores), ('hybrid', rrf(bow, scores))]:
            order = sorted(branch, key=lambda key: (-branch[key], key))
            results.append({'query_id': row['id'], 'family': row['family'], 'form': row['form'],
                'query': row['text'], 'language': row['language'], 'category': row['kind'],
                'method': method, 'variant': variants, 'encodes': 0 if method == 'bow' else len(encoded),
                'result_count': len(order), 'resource': quality(order, row['qrels']),
                'top10': [{'resource_id': key, 'score': branch[key]} for key in order[:10]],
                'best_raw_cosine': max(raw_scores.values()), 'pair_cosine': cosine(encoded[0], encoded[-1]),
                'timing_ms': {'normalization_variant': variant_ms, 'encoding': encode_ms,
                              'dense': dense_ms, 'bow': lexical_ms}})
        print(json.dumps({'completed_query_id': row['id']}), flush=True)
    summary = {}
    for method in ('bow', 'vector', 'hybrid'):
        for form in ('original', 'accentless', 'nfd', 'partial'):
            selected = [r for r in results if r['method'] == method and r['form'] == form]
            positive = [r for r in selected if r['resource']['has_positive']]
            negative = [r for r in selected if not r['resource']['has_positive']]
            summary[method + ':' + form] = {'n': len(selected), **{
                name: sum(r['resource'][name] for r in positive) / len(positive) if positive else None
                for name in ('precision1', 'recall5', 'mrr10', 'ndcg10')},
                'no_answer_fpr': sum(r['result_count'] > 0 for r in negative)/len(negative) if negative else None,
                'encoding_p50': percentile([r['timing_ms']['encoding'] for r in selected], .5),
                'encoding_p95': percentile([r['timing_ms']['encoding'] for r in selected], .95) if len(selected) >= 20 else None}
    report = {'status': 'DRAFT_DIAGNOSTIC', 'scope': 'offline public corpus; no HTTP/DB/ACL claim',
        'engine': args.engine, 'variant_policy': 'approved-missing-accents-v1' if args.variants else 'original',
        'model': {'id': 'BAAI/bge-m3', 'revision': args.revision, 'dimension': 1024},
        'source_revision': args.source_revision or subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        'corpus_checksum': checksum(fixture['documents']), 'qrels_checksum': checksum(suite(fixture)),
        'dictionary_fingerprint': dictionary.fingerprint, 'query_encodes': encodes,
        'lexical_variant_weight': policy.synonyms if args.variants else None,
        'summary': summary, 'results': results, 'evidence_quality': 'UNJUDGED',
        'token_count_truncation': 'NOT_INSTRUMENTED', 'http_latency': 'NOT_RUN'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--revision', default='5617a9f61b028005a4858fdac845db406aefb181')
    parser.add_argument('--engine', choices=['meaning_bag', 'semantic_chunk'], default='meaning_bag')
    parser.add_argument('--variants', action='store_true')
    parser.add_argument('--source-revision', help='Explicit revision for a QA source overlay without Git metadata')
    parser.add_argument('--output', required=True, type=Path)
    run(parser.parse_args())
