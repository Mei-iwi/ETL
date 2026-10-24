"""Read-only offline model probe on public prompt examples, independent of qrels."""
import argparse
import json
import math
from pathlib import Path
from time import perf_counter

from data_sync_etl.domain.evaluation import checksum
from data_sync_etl.domain.search import cosine, normalize_query


def run(args):
    if args.output.exists() or not args.output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('Fresh private .test-tmp output required')
    import torch
    torch.set_num_threads(2)
    from data_sync_etl.adapters.embeddings import LocalSentenceEmbedding
    revision = '5617a9f61b028005a4858fdac845db406aefb181'
    if args.engine == 'semantic_chunk':
        from data_sync_etl.adapters.bge_m3 import BgeM3Embedding
        model = BgeM3Embedding(args.model, revision)
        def encode(text):
            return model.embed_queries([text])[0]['vector']
    else:
        model = LocalSentenceEmbedding(args.model, 'BAAI/bge-m3', revision, 1024)
        encode = model.embed
    model.prepare()
    examples = json.loads(Path('fixtures/search/phase4_accent_pairs.json').read_text(encoding='utf-8'))
    tokenizer = model._model.tokenizer
    limit = model.max_tokens if args.engine == 'semantic_chunk' else model._model.max_seq_length
    rows = []
    for ordinal, pair in enumerate(examples['pairs'], 1):
        texts = list(map(normalize_query, pair))
        token_counts = [len(tokenizer.encode(text, truncation=False)) for text in texts]
        start = perf_counter()
        vectors = [encode(text) for text in texts]
        rows.append({'id': ordinal, 'queries': pair, 'token_counts': token_counts,
            'dimension': [len(vector) for vector in vectors], 'max_tokens': limit,
            'truncated': [count > limit for count in token_counts],
            'norms': [math.sqrt(math.fsum(v*v for v in vector)) for vector in vectors],
            'cosine_between_queries': cosine(*vectors), 'pair_encoding_ms': (perf_counter()-start)*1000})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump({'status': 'DIAGNOSTIC_NOT_RETRIEVAL_ACCEPTANCE', 'engine': args.engine,
            'model': 'BAAI/bge-m3', 'revision': revision, 'input_checksum': checksum(examples),
            'encodes': len(rows)*2, 'pairs': rows}, stream, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--engine', choices=['meaning_bag', 'semantic_chunk'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args())
