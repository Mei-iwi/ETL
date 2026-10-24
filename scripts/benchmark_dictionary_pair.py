"""Interleaved paired query-analysis profile with output parity; never HTTP/DB.

Baseline Python source is read from an explicit existing Git commit, not fetched.
Only the already trusted repository module is executed. No runtime flags change.
"""
import argparse
import importlib.util
import json
import random
import re
import subprocess
import sys
from pathlib import Path
from time import perf_counter

from data_sync_etl.domain.concepts import concept_analysis_scope, default_dictionary
from data_sync_etl.domain.evaluation import checksum, percentile
from data_sync_etl.domain.lexical import analysis_scope, bm25, lexical_document


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline-ref', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--repeats', type=int, choices=range(2, 31), default=10)
    p.add_argument('--scale', type=int, choices=(1, 10), default=1)
    args = p.parse_args()
    if (not re.fullmatch(r'[0-9a-f]{7,40}', args.baseline_ref) or args.output.exists()
            or not args.output.resolve().is_relative_to(Path('.test-tmp').resolve())):
        p.error('Explicit commit hash and new private output required')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    code = subprocess.run(['git', 'show', args.baseline_ref + ':src/data_sync_etl/domain/concepts.py'],
                          check=True, capture_output=True).stdout
    module_path = args.output.with_suffix('.baseline.py')
    module_path.write_bytes(code)
    spec = importlib.util.spec_from_file_location('hybrid_dictionary_baseline', module_path)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = baseline
    spec.loader.exec_module(baseline)
    current = default_dictionary()
    before = baseline.Dictionary(baseline.Snapshot.model_validate(current.snapshot.model_dump()))
    corpus = json.loads(Path('fixtures/search/concept_benchmark.json').read_text(encoding='utf-8'))
    texts = {d['id'] + ':' + str(i): d['text'] for d in corpus['documents'] for i in range(args.scale)}
    documents = {key: lexical_document([text]) for key, text in texts.items()}

    def workload(dictionary, query):
        with analysis_scope(), concept_analysis_scope():
            expansion = dictionary.expand(query['text'], domain=query.get('domain'))
            # Full-content matching, ranking, then evidence hydration. Repeated
            # text analysis is shared only inside this single simulated request.
            observed = [[(m.group(), cid) for m, cid in dictionary.observations(text)] for text in texts.values()]
            scores = bm25(query['text'], documents, dictionary=dictionary, expansion=expansion)
            evidence = [dictionary.evidence(expansion, text) for text in texts.values()]
            return checksum([scores, observed, evidence])

    # Same process, workload and randomized arm order, excluding cold CRF imports.
    workload(before, corpus['queries'][0])
    workload(current, corpus['queries'][0])
    samples, parity, rng = {'before': [], 'after': []}, True, random.Random(20261024)
    for _ in range(args.repeats):
        for query in corpus['queries']:
            arms = [('before', before), ('after', current)]
            rng.shuffle(arms)
            hashes = []
            for name, dictionary in arms:
                start = perf_counter()
                hashes.append(workload(dictionary, query))
                samples[name].append((perf_counter() - start) * 1000)
            parity &= hashes[0] == hashes[1]
    report = {'status': 'PASS' if parity else 'FAIL', 'output_parity': parity,
        'baseline_ref': args.baseline_ref, 'corpus_checksum': checksum(corpus),
        'dictionary_fingerprint': current.fingerprint, 'repeats': args.repeats,
        'scale': args.scale, 'documents': len(texts),
        'scope': 'warm query analysis/BM25/evidence; serial interleaved arms; no HTTP, DB or encoder',
        'arms': {name: {'p50_ms': percentile(times, .5), 'p95_ms': percentile(times, .95),
                        'samples': len(times)} for name, times in samples.items()}}
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not parity:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
