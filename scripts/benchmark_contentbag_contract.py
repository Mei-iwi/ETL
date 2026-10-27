"""Same-process synthetic lexical ablation; not real relevance/BGE evidence."""
import argparse
import json
import random
import subprocess
import types
from pathlib import Path
from time import perf_counter

from data_sync_etl.domain.lexical import bm25, lexical_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline-ref', default='070808f')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(Path('.test-tmp').resolve()) or output.exists():
        parser.error('New output under .test-tmp required')
    if len(args.baseline_ref) > 40 or any(c not in '0123456789abcdef' for c in args.baseline_ref):
        parser.error('Existing commit hash required')
    code = subprocess.run(['git', 'show', args.baseline_ref + ':src/data_sync_etl/domain/lexical.py'],
                          check=True, capture_output=True, text=True, encoding='utf-8').stdout
    baseline = types.ModuleType('isolated_lexical_baseline')
    exec(compile(code, '<trusted-repository-baseline>', 'exec'), baseline.__dict__)
    docs = {'math': lexical_document(['Cộng hai phân số khác mẫu cần quy đồng.']),
            'security': lexical_document(['Xác thực hai yếu tố bảo mật.'])}
    docs.update({f'filler{i}': lexical_document([f'unrelated mineral specimen {i}']) for i in range(48)})
    queries = ['Cộng hai phân số khác mẫu', 'Cong hai phan so khac mau',
               'specimen 17', 'unknown astronomical wormhole', 'SQL NoSQL C#']
    engines = {'before_raw': baseline.bm25, 'after_raw': bm25,
               'after_corpus_accent_opt_in': lambda q, d: bm25(q, d, accent_insensitive=True)}
    # Warm analyzer outside measured ranking calls, interleave order.
    for engine in engines.values():
        engine(queries[0], docs)
    samples = {name: [] for name in engines}
    rankings = {name: {} for name in engines}
    rng = random.Random(27)
    for _ in range(20):
        for query in queries:
            arms = list(engines.items())
            rng.shuffle(arms)
            for name, engine in arms:
                started = perf_counter()
                scores = engine(query, docs)
                samples[name].append((perf_counter() - started) * 1000)
                rankings[name][query] = sorted(scores, key=lambda key: (-scores[key], key))[:5]
    def percentile(values, p):
        return sorted(values)[round((len(values) - 1) * p)]
    result = {'classification': 'DEVELOPMENT_FIXTURE_ONLY', 'baseline_ref': args.baseline_ref,
              'documents': len(docs), 'calls_per_arm': len(queries) * 20,
              'model': 'NONE', 'qrels': 'QUALITY_PENDING', 'latency_ms': {
                  name: {f'p{int(p * 100)}': percentile(values, p) for p in (.5, .95, .99)}
                  for name, values in samples.items()}, 'rankings': rankings}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'rankings'}))


if __name__ == '__main__':
    main()
