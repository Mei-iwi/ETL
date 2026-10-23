"""Offline frozen-corpus experiments. No DB writes, no runtime flag changes.

Run with --help. Output contains query/annotation data: use a private local path
unless the evaluation corpus is explicitly suitable for sharing.
"""
import argparse
import json
import os
import time
from pathlib import Path

from data_sync_etl.application.vector_quality import (
    calibrate_threshold,
    compile_qrels,
    diverse_candidates,
    evaluate_ranking,
    grouped_hits,
    score_distribution,
    summarize,
    validate_scores,
)
from data_sync_etl.domain.evaluation import checksum
from data_sync_etl.domain.keywords import UnigramKeywords
from data_sync_etl.domain.search import binary_score, normalize_query
from data_sync_etl.domain.semantic import embedding_input


def run(args):
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import numpy as np
    import psutil
    import torch

    from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
    torch.set_num_threads(args.threads)
    corpus = json.loads(args.corpus.read_text(encoding='utf-8'))
    if len(corpus['resources']) > 100 or args.corpus.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('Bounded evaluation corpus required')
    actual_checksum = checksum({key: value for key, value in corpus.items() if key != 'checksum'})
    if actual_checksum != corpus['checksum']:
        raise ValueError('Corpus checksum mismatch')
    audit_path = args.audit or args.corpus.with_name('audit.json')
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    expected_model = 'BAAI/bge-m3@' + args.revision
    if (audit.get('corpus_checksum') != corpus['checksum'] or not audit.get('bag_models')
            or any(row['_id']['model'] != expected_model or row['_id']['dimension'] != 1024
                   for row in audit['bag_models'])):
        raise ValueError('Audit/model/checkpoint mismatch')
    specification = json.loads(args.spec.read_text(encoding='utf-8'))
    qrels = compile_qrels(corpus, specification)
    queries = qrels['queries']
    if not 1 <= len(queries) <= 100:
        raise ValueError('Bounded query set required')
    chunks = {}
    for resource in corpus['resources']:
        for chunk in resource['chunks']:
            if chunk['id'] in chunks:
                raise ValueError('Duplicate chunk ID')
            chunks[chunk['id']] = {**chunk, 'resource_id': resource['resource_id'],
                'version_id': resource['version_id'], 'metadata': resource['metadata']}
    if len(chunks) > 2000:
        raise ValueError('Bounded evaluation candidate set required')
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'qrels.json').write_text(json.dumps(qrels, ensure_ascii=False, indent=2), encoding='utf-8')
    model = BgeM3Embedding(args.model_path, args.revision, max_tokens=args.max_tokens,
                           batch_size=args.batch_size, timeout_seconds=300)
    start = time.perf_counter()
    model.prepare()
    cpu_start = time.process_time()
    report = {'corpus_checksum': corpus['checksum'], 'label_status': qrels['label_status'],
        'model': model.model_identity(), 'model_load_seconds': time.perf_counter() - start,
        'resources': len(corpus['resources']), 'chunks': len(chunks), 'queries': len(queries),
        'scope': 'OFFLINE_READONLY_SNAPSHOT_NOT_LIVE_AUTHENTICATED_API',
        'methods': {}, 'rows': [], 'rerank': [],
        'latency_scope': 'offline_ranking_excludes_query_encode_and_live_database_and_HTTP',
        'live_database_retrieval_ms': None, 'live_http_serialization_ms': None}

    def encode(texts, name):
        key = checksum([corpus['checksum'], model.model_identity(), texts, name])
        artifact = args.output / (name + '-' + key[:16] + '.npz')
        started = time.perf_counter()
        if artifact.exists():
            with np.load(artifact, allow_pickle=False) as data:
                result = data['vectors']
            if (result.shape != (len(texts), 1024) or not np.isfinite(result).all()
                    or not np.allclose(np.linalg.norm(result, axis=1), 1., atol=1e-5)):
                raise ValueError('Invalid evaluation artifact')
            return result, {'artifact_reused': True, 'seconds': None, 'artifact_bytes': artifact.stat().st_size}
        encoded, batch_ms = [], []
        for offset in range(0, len(texts), model.batch_size):
            batch_start = time.perf_counter()
            batch = model.embed_documents(texts[offset:offset + model.batch_size])
            batch_ms.append((time.perf_counter() - batch_start) * 1000)
            encoded.extend(row['vector'] for row in batch)
            if offset % 40 == 0:
                print(json.dumps({'phase': name, 'completed': min(offset + model.batch_size, len(texts)),
                                  'total': len(texts)}), flush=True)
        result = np.asarray(encoded, dtype=np.float32)
        np.savez_compressed(artifact, vectors=result)
        return result, {'artifact_reused': False, 'seconds': time.perf_counter() - started,
                       'artifact_bytes': artifact.stat().st_size, 'batch_ms': batch_ms}

    started = time.perf_counter()
    normalized_queries = [normalize_query(row['query']) for row in queries]
    report['query_preprocessing_ms'] = (time.perf_counter() - started) * 1000
    query_vectors, report['query_encoding'] = encode(normalized_queries, 'queries')
    all_scores = {}
    keyword = UnigramKeywords()
    for strategy in ('legacy', 'raw', 'contextual', 'title_only'):
        if strategy == 'legacy':
            entries = [(resource, bag) for resource in corpus['resources'] for bag in resource['bags']]
            keys = [bag['id'] for _, bag in entries]
            vectors = np.asarray([bag['vector'] for _, bag in entries], dtype=np.float32)
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            if vectors.shape[1] != 1024 or not np.isfinite(vectors).all() or (norms == 0).any():
                raise ValueError('Invalid legacy vectors')
            vectors /= norms
            measurement = {'source': 'existing_local_mongo_vectors', 'seconds': None, 'artifact_bytes': None}
        else:
            keys = list(chunks)
            texts = []
            for key in keys:
                chunk, metadata = chunks[key], chunks[key]['metadata']
                texts.append(embedding_input(chunk, title=metadata.get('title'),
                    subjects=metadata.get('subjects', ()) if strategy == 'contextual' else (),
                    grades=metadata.get('grades', ()) if strategy == 'contextual' else (),
                    headings=chunk['headings'] if strategy == 'contextual' else (),
                    strategy='raw' if strategy == 'raw' else 'contextual'))
            vectors, measurement = encode(texts, strategy)
        rows = []
        for qi, query in enumerate(queries):
            started = time.perf_counter()
            scores = dict(zip(keys, np.clip(vectors @ query_vectors[qi], -1., 1.).astype(float), strict=True))
            validate_scores(list(scores.values()))
            similarity_ms = (time.perf_counter() - started) * 1000
            filtering_start = time.perf_counter()
            if strategy == 'legacy':
                query_keywords = keyword.extract(normalize_query(query['query']))
                locators = {}
                for resource, bag in entries:
                    members = [chunk for chunk in resource['chunks'] if chunk['bag_id'] == bag['id']]
                    if not members:
                        continue
                    chunk = max(members, key=lambda chunk: binary_score(query_keywords, keyword.extract(chunk['text']))[0])
                    locators[bag['id']] = {**chunk, 'chunk_id': chunk['id'],
                        'resource_id': resource['resource_id'], 'version_id': resource['version_id']}
                scores = {key: value for key, value in scores.items() if key in locators}
            else:
                locators = chunks
            filtering_ms = (time.perf_counter() - filtering_start) * 1000
            grouping_start = time.perf_counter()
            hits = grouped_hits({key: score for key, score in scores.items() if score > 0.}, locators)
            grouping_ms = (time.perf_counter() - grouping_start) * 1000
            chunk_order = sorted(scores, key=lambda key: (-scores[key], key))
            chunk_ids = [locators[key].get('chunk_id', key) for key in chunk_order if scores[key] > 0.]
            row = {'query_id': query['query_id'], 'query': query['query'], 'language': query['language'],
                'category': query['category'], 'split': query['split'], 'strategy': strategy,
                'qrels': query['resource_qrels'], 'hits': hits,
                'metrics': evaluate_ranking([hit['id'] for hit in hits], query['resource_qrels']),
                'chunk_metrics': evaluate_ranking(chunk_ids, query['chunk_qrels']),
                'stages_ms': {'similarity': similarity_ms, 'snippet_filtering': filtering_ms, 'grouping': grouping_ms},
                'latency_ms': (time.perf_counter() - started) * 1000,
                'chunk_metric_semantics': 'displayed_lexical_snippets' if strategy == 'legacy' else 'dense_chunks'}
            rows.append(row)
            if strategy == 'contextual':
                all_scores[query['query_id']] = {key: score for key, score in scores.items() if score > 0.}
        calibration = calibrate_threshold([row for row in rows if row['split'] == 'validation'])
        threshold = calibration['selected']['threshold']
        filtered = []
        for row in rows:
            hits = [hit for hit in row['hits'] if hit['score'] > threshold]
            filtered.append({**row, 'hits': hits,
                'metrics': evaluate_ranking([hit['id'] for hit in hits], row['qrels'])})
        by_group = {}
        for field in ('split', 'language', 'category'):
            by_group[field] = {value: summarize([row for row in rows if row[field] == value])
                               for value in sorted({row[field] for row in rows})}
        report['methods'][strategy] = {'summary': summarize(rows), 'groups': by_group,
            'chunk_summary': summarize([{**row, 'metrics': row['chunk_metrics']} for row in rows]),
            'threshold_experiment': {'calibration': calibration,
                'test': summarize([row for row in filtered if row['split'] == 'test'])},
            'score_distribution': score_distribution(rows), 'encoding': measurement,
            'rss_bytes': psutil.Process().memory_info().rss}
        report['rows'].extend(rows)
        (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'phase': strategy, 'summary': summarize(rows)}, ensure_ascii=True), flush=True)

    # Bounded ColBERT comparison, same candidate union encoded once per query.
    selected = set(args.rerank_query_ids.split(',')) if args.rerank_query_ids else set()
    version_by_key = {key: chunk['version_id'] for key, chunk in chunks.items()}
    for query in queries:
        if query['query_id'] not in selected:
            continue
        scores = all_scores[query['query_id']]
        bag_best = {}
        for key in sorted(scores, key=lambda key: (-scores[key], key)):
            bag_best.setdefault(chunks[key]['bag_id'], key)
        bag_scores = {bag: scores[key] for bag, key in bag_best.items()}
        bag_versions = {bag: version_by_key[key] for bag, key in bag_best.items()}
        bag_keys = [bag_best[bag] for bag in sorted(bag_scores, key=lambda bag: (-bag_scores[bag], bag))]
        diverse_keys = [bag_best[bag] for bag in diverse_candidates(bag_scores, bag_versions, 50)]
        union = set(bag_keys[:50]) | set(diverse_keys)
        union = sorted(union)
        started = time.perf_counter()
        values = model.rerank(query['query'], [chunks[key]['text'] for key in union[:50]])
        if len(union) > 50:
            values.extend(model.rerank(query['query'], [chunks[key]['text'] for key in union[50:]]))
        colbert = dict(zip(union, values, strict=True))
        seconds = time.perf_counter() - started
        for limit in (10, 20, 50):
            for policy in ('bag_first', 'version_diverse'):
                candidates = bag_keys[:limit] if policy == 'bag_first' else diverse_keys[:limit]
                hits = grouped_hits({key: colbert[key] for key in candidates}, chunks)
                dense_hits = grouped_hits({key: scores[key] for key in candidates}, chunks)
                report['rerank'].append({'query_id': query['query_id'], 'limit': limit, 'policy': policy,
                    'candidates': len(candidates), 'unique_versions': len({version_by_key[key] for key in candidates}),
                    'metrics': evaluate_ranking([hit['id'] for hit in hits], query['resource_qrels']),
                    'candidate_metrics': evaluate_ranking([hit['id'] for hit in dense_hits], query['resource_qrels']),
                    'hits': hits[:10], 'union_encoding_seconds': seconds,
                    'timing_scope': 'shared_union_not_independent_top_k_latency'})
        print(json.dumps({'phase': 'colbert', 'query_id': query['query_id'], 'union_candidates': len(union),
                          'seconds': seconds}), flush=True)
    report['experiment_cpu_seconds'] = time.process_time() - cpu_start
    started = time.perf_counter()
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    report['offline_report_serialization_ms'] = (time.perf_counter() - started) * 1000
    report['offline_report_bytes'] = len(serialized.encode('utf-8'))
    (args.output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'PARTIAL' if 'DRAFT' in qrels['label_status'] else 'PASS',
        'report': str(args.output / 'report.json'), 'runtime_configuration_changed': False}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--audit', type=Path, help='Matching READ ONLY export audit (default: corpus sibling audit.json)')
    parser.add_argument('--model-path', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--max-tokens', type=int, default=1024, choices=(256, 512, 1024))
    parser.add_argument('--batch-size', type=int, default=2, choices=(1, 2, 4))
    parser.add_argument('--threads', type=int, default=2, choices=(1, 2, 4))
    parser.add_argument('--rerank-query-ids', default='B2,E1')
    run(parser.parse_args())


if __name__ == '__main__':
    main()
