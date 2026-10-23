"""Offline threshold sweep on the small labelled development corpus.

Uses production's keyword-joined dense representation, not synthetic vectors.
No databases, downloads, model changes or modifications to .env.
"""

import argparse
import json
import os
from pathlib import Path

from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.keywords import UnigramKeywords
from data_sync_etl.domain.search import cosine


def benchmark(args):
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    import torch

    torch.set_num_threads(2)
    corpus = json.loads(args.corpus.read_text(encoding='utf-8'))
    revision = (args.model / 'etl-model-revision.txt').read_text(encoding='utf-8').strip()
    model = LocalSentenceEmbedding(args.model, args.model_id, revision, args.dimension)
    model.prepare()
    extractor = UnigramKeywords()
    vectors = {doc['id']: model.embed(' | '.join(extractor.extract(doc['text'])))
               for doc in corpus['documents']}
    pairs = []
    for query in corpus['queries']:
        vector = model.embed(query['text'])
        pairs.extend({'query_id': query['id'], 'document_id': key,
                      'relevant': query['qrels'].get(key, 0) > 0, 'cosine': cosine(vector, value)}
                     for key, value in vectors.items())
        print(query['id'], flush=True)
    sweep = []
    for threshold in [0.0, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7]:
        accepted = [pair for pair in pairs if pair['cosine'] > 0 and pair['cosine'] >= threshold]
        tp = sum(pair['relevant'] for pair in accepted)
        positives = sum(pair['relevant'] for pair in pairs)
        sweep.append({'threshold': threshold, 'accepted': len(accepted),
                      'labelled_positive_hits': tp, 'unlabelled_hits': len(accepted) - tp,
                      'precision': tp / len(accepted) if accepted else 0,
                      'recall': tp / positives if positives else 0})
    return {'model': model.model_id, 'revision': revision, 'dimension': args.dimension,
            'corpus_sha256': digest(corpus), 'dense_input': 'production keyword-joined top5',
            'limitations': ['Small development fixture; thresholds do not generalize to every corpus/model.',
                            'Unlabelled pairs are not independently confirmed irrelevant.'],
            'sweep': sweep, 'pairs': pairs}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus', type=Path, default=Path('fixtures/search/lexical_benchmark.json'))
    parser.add_argument('--model', type=Path, default=Path('Models/bge-m3'))
    parser.add_argument('--model-id', default='BAAI/bge-m3')
    parser.add_argument('--dimension', type=int, default=1024)
    parser.add_argument('--output', type=Path, default=Path('storage/benchmarks/vector-filter.json'))
    args = parser.parse_args()
    result = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result['sweep'], indent=2))


if __name__ == '__main__':
    main()
