"""Blinded metadata-field review package; no fake Chunk and no label approval.

Uses only the repository's public synthetic fixture. Output is private and fresh.
Human resource/evidence grades and full-corpus answerability remain unjudged.
"""
import argparse
import json
from pathlib import Path

from data_sync_etl.application.search_review import (
    Judgment,
    ReviewQuery,
    check_splits,
    write_review,
)
from data_sync_etl.domain.indexing import digest


def prepare(output):
    if not output.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('Private fresh review directory required')
    source = json.loads(Path('fixtures/search/multilingual_context.json').read_text(encoding='utf-8'))
    cases = [
        ('fraction', 'Cộng hai phân số khác mẫu', 'vi', 'exact'),
        ('fraction', 'Cong hai phan so khac mau', 'vi', 'accentless'),
        ('fraction', 'How to add fractions with different denominators?', 'en', 'paraphrase'),
        ('fraction', 'Comment additionner deux fractions ?', 'fr', 'paraphrase'),
        ('fraction', 'video cách add fractions khác mẫu', 'mixed', 'code_mixed'),
        ('subtract', 'Trừ hai phân số khác mẫu', 'vi', 'hard_negative'),
        ('plant', 'Vai trò ánh sáng trong quang hợp', 'vi', 'exact'),
        ('plant', 'Vai tro anh sang trong quang hop', 'vi', 'accentless'),
        ('plant', 'photosynthesis in green leaves', 'en', 'paraphrase'),
        ('respiration', 'Hô hấp ở thực vật', 'vi', 'hard_negative'),
        ('plant-negative', 'Quá trình không cần ánh sáng', 'vi', 'negation'),
        ('ml', 'Học máy là gì?', 'vi', 'terminology'),
        ('ml', 'ML machine learning', 'en', 'acronym'),
        ('cpp', 'C++ và C#', 'mixed', 'exact_identifier'),
        ('python-language', 'Python programming language', 'en', 'polysemy'),
        ('python-animal', 'python snake habitat', 'en', 'polysemy'),
        ('no-answer', 'quantum cryptography teleportation', 'en', 'no_answer_candidate'),
        ('no-answer-fr', 'cryptographie quantique intrication', 'fr', 'no_answer_candidate'),
    ]
    queries = [ReviewQuery(id=f'metadata-q-{i:03d}', text=text, language=language, category=category,
        intent_family_id='metadata-draft-' + family, split='diagnostic')
        for i, (family, text, language, category) in enumerate(cases)]
    split_audit = check_splits(queries)
    # Public bodies become explicitly SYNTHETIC description fields, not a claim
    # that any live MASTER extension has these values. No SOURCE/DB read.
    corpus = [{'resource_id': doc['id'], 'version_id': doc['id'] + '-v1',
               'fields': {'TITLE': doc['title'], 'DESCRIPTION': doc['text']}}
              for doc in source['documents']]
    package = {'schema': 'metadata-field-review-v1', 'review_status': 'REVIEW_PENDING',
        'provenance': 'public_synthetic_fixture', 'corpus_fingerprint': digest(corpus),
        'queries': [query.model_dump() for query in queries], 'corpus': corpus,
        'model': {'id': 'BAAI/bge-m3', 'dimension': 1024, 'inference_run': False},
        'query_split_audit': split_audit,
        'limitations': ['DRAFT intent families require independent review',
            'No holdout released', 'No author policy', 'No transcript or content locator',
            'No-answer candidates are UNJUDGED, never assumed negatives']}
    package['checksum'] = digest(package)
    items, judgments = [], []
    for query in queries:
        for resource in corpus:
            for field, text in resource['fields'].items():
                key = digest([query.id, resource['resource_id'], resource['version_id'], field, text])
                items.append({'item_id': key, 'query_id': query.id, 'resource_id': resource['resource_id'],
                    'resource_version_id': resource['version_id'], 'metadata_field': field, 'evidence': text,
                    'evidence_kind': 'METADATA_FIELD', 'chunk_id': None, 'content_unit_id': None, 'bag_id': None})
                judgments.append(Judgment(package_checksum=package['checksum'], item_id=key).model_dump())
    write_review(output / 'package.json', package)
    write_review(output / 'items.jsonl', items, jsonl=True)
    write_review(output / 'judgments.jsonl', judgments, jsonl=True)
    return {'status': 'REVIEW_PENDING', 'queries': len(queries), 'resources': len(corpus),
            'items': len(items), 'approved_judgments': 0, 'holdout': 'BLOCKED_REVIEW_PENDING'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args.output)))
        return 0
    except (ValueError, OSError):
        print(json.dumps({'status': 'BLOCKED', 'code': 'PRIVATE_FRESH_OUTPUT_REQUIRED'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
