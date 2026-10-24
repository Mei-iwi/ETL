"""Blinded human-review packages and leakage checks; never approve qrels.

Inputs must come from an authorized export or a public isolated fixture. This
module validates identity/integrity, NOT rights to arbitrary supplied content.
"""
import json
import random
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from data_sync_etl.domain.indexing import digest


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, hide_input_in_errors=True)


class ReviewQuery(Strict):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)
    language: str = Field(min_length=1, max_length=20)
    category: str = Field(min_length=1, max_length=80)
    intent_family_id: str = Field(min_length=1, max_length=80)
    split: Literal['diagnostic', 'development', 'validation', 'holdout']


def check_splits(queries):
    identities, families, texts = set(), {}, {}
    for q in queries:
        key = ' '.join(unicodedata.normalize('NFC', q.text).casefold().split())
        if (q.id in identities or families.get(q.intent_family_id, q.split) != q.split
                or texts.get(key, q.split) != q.split):
            raise ValueError('Duplicate identity or cross-split query/intent leakage')
        identities.add(q.id)
        families[q.intent_family_id], texts[key] = q.split, q.split
    return {'queries': len(identities), 'intent_families': len(families), 'holdout_status': 'BLOCKED_REVIEW_PENDING'}


class ReviewCandidate(Strict):
    resource_id: str = Field(min_length=1, max_length=64)
    version_id: str = Field(min_length=1, max_length=64)
    chunk_id: str = Field(min_length=1, max_length=64)
    bag_id: str = Field(min_length=1, max_length=64)
    content_unit_id: str = Field(min_length=1, max_length=64)
    title: str = Field(max_length=1000)
    version_number: int | None = Field(default=None, ge=1)
    subject: str = Field(default='', max_length=300)
    grade_level: str = Field(default='', max_length=100)
    evidence: str = Field(max_length=20000)
    snippet: str = Field(max_length=1000)
    content_fingerprint: str = Field(pattern=r'^[0-9a-f]{64}$')
    page_from: int | None = None
    page_to: int | None = None


class Judgment(Strict):
    package_checksum: str = Field(pattern=r'^[0-9a-f]{64}$')
    item_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    reviewer_id: str = Field(default='', max_length=80)
    evaluated_at: str = ''
    status: Literal['DRAFT', 'REVIEWED', 'ADJUDICATED', 'APPROVED'] = 'DRAFT'
    resource_grade: int | None = Field(default=None, ge=0, le=3)
    evidence_grade: int | None = Field(default=None, ge=0, le=3)
    snippet_grade: int | None = Field(default=None, ge=0, le=3)
    answerability: Literal['INSUFFICIENTLY_JUDGED', 'DIRECT', 'PARTIAL_TOPIC', 'NO_DIRECT', 'NO_RELATED', 'AMBIGUOUS'] = 'INSUFFICIENTLY_JUDGED'
    corpus_coverage: Literal['pool_only', 'full_authorized_corpus'] = 'pool_only'
    uncertain: bool = False
    missing_candidate: bool = False
    notes: str = Field(default='', max_length=2000)

    @model_validator(mode='after')
    def reviewed(self):
        if self.status != 'DRAFT':
            if (not re.fullmatch(r'[a-zA-Z0-9_-]{2,80}', self.reviewer_id) or not self.notes.strip()
                    or (not self.uncertain and self.resource_grade is None)):
                raise ValueError('Reviewed record requires pseudonymous reviewer, notes and grade/uncertainty')
            stamp = datetime.fromisoformat(self.evaluated_at.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Timezone-aware review timestamp required')
        if self.answerability in ('NO_DIRECT', 'NO_RELATED') and self.corpus_coverage != 'full_authorized_corpus':
            raise ValueError('Top-K pool cannot establish corpus-wide absence of answers')
        if ((self.evidence_grade or 0) >= 2 and (self.resource_grade or 0) < 2
                or (self.snippet_grade or 0) >= 2 and (self.evidence_grade or 0) < 2
                or self.answerability in ('NO_DIRECT', 'NO_RELATED') and (self.resource_grade or 0) >= 2):
            raise ValueError('Contradictory resource/evidence/answerability grades')
        return self


def review_package(queries, sources, *, corpus_fingerprint, model, dictionary_revision,
                   evaluation_policy, provenance, seed=20261024):
    if provenance not in ('public_fixture', 'authorized_staging_export'):
        raise ValueError('Explicit allowed evidence provenance required')
    if not queries or len(queries) > 200 or len(sources) > 10:
        raise ValueError('Bounded review package required')
    check_splits(queries)
    known = {q.id for q in queries}
    unique, provenance_map = {}, defaultdict(list)
    for source, rows in sources.items():
        for query_id, candidate in rows:
            if query_id not in known:
                raise ValueError('Unknown query identity')
            key = (query_id, candidate.resource_id, candidate.version_id, candidate.chunk_id)
            if key in unique and unique[key] != candidate:
                raise ValueError('Conflicting candidate identity/content')
            unique[key] = candidate
            provenance_map[digest(key)].append(source)
    if not unique or len(unique) > 30000:
        raise ValueError('Bounded nonempty review pool required')
    items = [{'item_id': digest(key), 'query_id': key[0], 'candidate': row.model_dump()}
             for key, row in sorted(unique.items())]
    random.Random(seed).shuffle(items)
    package = {'kind': 'search-human-review-v1', 'revision': 1, 'provenance': provenance,
        'corpus_fingerprint': corpus_fingerprint, 'model': model, 'dictionary_revision': dictionary_revision,
        'evaluation_policy': evaluation_policy, 'metrics': 'graded-resource-evidence-separate-unjudged-v1',
        'queries': [q.model_dump() for q in queries], 'items': items, 'status': 'REVIEW_PENDING',
        'independent_holdout': 'BLOCKED_REVIEW_PENDING'}
    package['checksum'] = digest(package)
    judgments = [Judgment(package_checksum=package['checksum'], item_id=row['item_id']).model_dump() for row in items]
    # Rank/method/score and previous draft grades are deliberately outside the
    # blinded reviewer package; retain them only in the operator's private audit.
    return package, judgments, dict(provenance_map)


def verify_package(package):
    if (package.get('kind') != 'search-human-review-v1'
            or digest({k: v for k, v in package.items() if k != 'checksum'}) != package.get('checksum')):
        raise ValueError('Review package integrity mismatch')
    queries = [ReviewQuery.model_validate(q) for q in package['queries']]
    check_splits(queries)
    known_queries = {q.id for q in queries}
    seen = set()
    for item in package['items']:
        candidate = ReviewCandidate.model_validate(item['candidate'])
        key = (item['query_id'], candidate.resource_id, candidate.version_id, candidate.chunk_id)
        if item['query_id'] not in known_queries or item['item_id'] != digest(key) or item['item_id'] in seen:
            raise ValueError('Review candidate identity mismatch')
        seen.add(item['item_id'])
    return seen


def audit_judgments(package, rows):
    known = verify_package(package)
    seen, grades, counts = set(), defaultdict(list), defaultdict(int)
    for value in rows:
        row = Judgment.model_validate(value)
        if row.package_checksum != package['checksum'] or row.item_id not in known:
            raise ValueError('Judgment package/item mismatch')
        # A JSON status is NOT a human signature or trusted approval identity.
        if row.status in ('ADJUDICATED', 'APPROVED'):
            raise ValueError('Approval/adjudication requires designated authenticated external review authority')
        key = row.item_id, row.reviewer_id
        if key in seen:
            raise ValueError('Duplicate review by the same reviewer')
        seen.add(key)
        counts[row.status] += 1
        if row.status == 'REVIEWED' and not row.uncertain:
            grades[row.item_id].append((row.resource_grade, row.evidence_grade, row.snippet_grade))
    compared = [v for v in grades.values() if len(v) > 1]
    conflicts = [k for k, v in grades.items() if len(set(v)) > 1]
    return {'status': 'REVIEW_PENDING', 'status_counts': dict(counts), 'reviewed_items': len(grades),
        'unjudged_items': len(known - grades.keys()), 'conflicts': conflicts,
        'agreement': sum(len(set(v)) == 1 for v in compared) / len(compared) if compared else None,
        'automatic_promotion': False, 'independent_holdout': 'BLOCKED_REVIEW_PENDING'}


def freeze_review(package, selection_policy, judgments=None):
    """Freeze identities/policy BEFORE any final evaluation, not human approval.

    Existing diagnostic packages remain diagnostic even after a checksum freeze.
    Release of an independent holdout requires external reviewers/authority.
    """
    verify_package(package)
    rows = judgments if judgments is not None else [Judgment(package_checksum=package['checksum'],
        item_id=item['item_id']).model_dump() for item in package['items']]
    audit = audit_judgments(package, rows)
    record = {'kind': 'search-review-freeze-v1', 'package_checksum': package['checksum'],
        'corpus_fingerprint': package['corpus_fingerprint'], 'queries_checksum': digest(package['queries']),
        'qrels_checksum': digest(rows), 'judgment_status_counts': audit['status_counts'],
        'model': package['model'], 'dictionary_revision': package['dictionary_revision'],
        'metrics': package['metrics'], 'selection_policy': selection_policy,
        'review_status': 'REVIEW_PENDING', 'holdout_release': 'BLOCKED_REVIEW_PENDING'}
    record['checksum'] = digest(record)
    return record


def write_review(path, value, *, jsonl=False):
    path = Path(path)
    if path.exists() or not path.resolve().is_relative_to(Path('.test-tmp').resolve()):
        raise ValueError('Fresh private output required; do not overwrite review history')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        if jsonl:
            for row in value:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            json.dump(value, stream, ensure_ascii=False, indent=2)
