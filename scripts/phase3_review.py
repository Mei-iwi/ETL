"""Prepare a blinded PUBLIC fixture review or audit private JSONL judgments.

No DB access, label promotion, new holdout claims, model work or runtime changes.
"""
import argparse
import json
from pathlib import Path

from data_sync_etl.application.search_review import (
    ReviewCandidate,
    ReviewQuery,
    audit_judgments,
    review_package,
    write_review,
)
from data_sync_etl.domain.concepts import empty_dictionary
from data_sync_etl.domain.indexing import digest


def prepare(output):
    base = json.loads(Path('fixtures/search/multilingual_context.json').read_text(encoding='utf-8'))
    extra = json.loads(Path('fixtures/search/phase2_queries.json').read_text(encoding='utf-8'))
    all_queries = base['queries'] + extra['queries']
    queries = [ReviewQuery(id=q['id'], text=q['text'], language=q['language'], category=q['kind'],
        intent_family_id='unassigned-diagnostic-' + q['id'], split='diagnostic') for q in all_queries]
    candidates = [ReviewCandidate(resource_id=d['id'], version_id=d['id'] + '-v1', bag_id=d['id'] + '-bag',
        content_unit_id=d['id'] + '-unit', chunk_id=d['id'] + '-chunk', title=d['title'],
        evidence=d['text'], snippet=d['text'][:400], content_fingerprint=digest(d)) for d in base['documents']]
    # All public documents, not only one retriever's favorite hits. The labels in
    # either fixture are intentionally not copied into the blinded package.
    sources = {'public_full_corpus': [(q.id, c) for q in queries for c in candidates]}
    package, rows, audit = review_package(queries, sources, corpus_fingerprint=digest(base['documents']),
        model={'id': 'BAAI/bge-m3', 'dimension': 1024, 'inference_run': False},
        dictionary_revision=empty_dictionary().fingerprint,
        evaluation_policy={'direct_grade_min': 2, 'unjudged': 'UNKNOWN_NOT_ZERO', 'new_thresholds': 'OFF',
                           'intent_families': 'REVIEW_PENDING_DIAGNOSTIC_ONLY'}, provenance='public_fixture')
    output = Path(output)
    if output.exists():
        raise ValueError('Fresh private review directory required')
    # Preflight path BEFORE creating any artifact.
    if not output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('Private review directory required')
    write_review(output/'package.json', package)
    write_review(output/'judgments.jsonl', rows, jsonl=True)
    write_review(output/'operator-pool-audit.json', audit)
    return {'status': 'REVIEW_PENDING', 'queries': len(queries), 'items': len(rows),
            'checksum': package['checksum'], 'independent_holdout': 'BLOCKED_REVIEW_PENDING'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('prepare-public', 'validate'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--package', type=Path)
    parser.add_argument('--reviews', nargs='+', type=Path)
    args = parser.parse_args()
    try:
        if args.operation == 'prepare-public':
            result = prepare(args.output)
        else:
            paths = [args.package, *(args.reviews or [])]
            if (not args.package or not args.reviews or any(
                not p.resolve().is_relative_to(Path('.test-tmp').resolve()) or p.stat().st_size > 64*1024*1024
                    for p in paths)):
                raise ValueError('Bounded private review inputs required')
            package = json.loads(args.package.read_text(encoding='utf-8'))
            rows = [json.loads(line) for p in args.reviews for line in p.read_text(encoding='utf-8').splitlines() if line.strip()]
            if len(rows) > 60000:
                raise ValueError('Bounded review import required')
            result = audit_judgments(package, rows)
            write_review(args.output, result)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, KeyError):
        print(json.dumps({'status': 'BLOCKED', 'code': 'INVALID_REVIEW_INPUT_OR_AUTHORITY_REQUIRED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
