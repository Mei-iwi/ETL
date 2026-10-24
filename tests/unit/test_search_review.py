from copy import deepcopy

import pytest
from pydantic import ValidationError

from data_sync_etl.application.search_review import (
    Judgment,
    ReviewCandidate,
    ReviewQuery,
    audit_judgments,
    check_splits,
    freeze_review,
    review_package,
    verify_package,
)


def query(**updates):
    return ReviewQuery(id='q1', text='How do leaves make food?', language='en', category='cross-language',
                       intent_family_id='photosynthesis', split='validation').model_copy(update=updates)


def package():
    candidate = ReviewCandidate(resource_id='r', version_id='v', bag_id='b', content_unit_id='u', chunk_id='c',
        title='Plants', evidence='Leaves use light.', snippet='Leaves use light.', content_fingerprint='a'*64)
    return review_package([query()], {'bow': [('q1', candidate)], 'dense': [('q1', candidate)]},
        corpus_fingerprint='b'*64, model={'id': 'BAAI/bge-m3', 'dimension': 1024}, dictionary_revision='c'*64,
        evaluation_policy={'threshold': 'OFF'}, provenance='public_fixture')


def test_blind_pool_deduplicates_methods_and_never_guesses_unjudged_grade():
    p, rows, audit = package()
    assert len(p['items']) == 1 and len(next(iter(audit.values()))) == 2
    assert all(k not in str(p) for k in ('draft_grade', 'rank', 'bow', 'dense'))
    assert verify_package(p)
    assert rows[0]['resource_grade'] is None and rows[0]['evidence_grade'] is None
    assert audit_judgments(p, rows)['unjudged_items'] == 1
    freeze = freeze_review(p, {'precision1': 'must_improve', 'recall5': 'must_not_decrease'})
    assert freeze['package_checksum'] == p['checksum'] and freeze['holdout_release'] == 'BLOCKED_REVIEW_PENDING'
    assert freeze['judgment_status_counts'] == {'DRAFT': 1}
    assert freeze_review(p, {}, [{**rows[0], 'notes': 'new revision'}])['qrels_checksum'] != freeze['qrels_checksum']
    changed = deepcopy(p)
    changed['items'][0]['candidate']['evidence'] = 'tampered'
    with pytest.raises(ValueError):
        verify_package(changed)


def test_intent_families_and_normalized_query_leakage_are_blocked():
    with pytest.raises(ValueError):
        check_splits([query(), query(id='q2', text='Different words', split='holdout')])
    with pytest.raises(ValueError):
        check_splits([query(), query(id='q2', intent_family_id='other', split='holdout')])
    assert check_splits([query()])['holdout_status'] == 'BLOCKED_REVIEW_PENDING'


def test_review_disagreement_timestamps_no_answer_and_forged_approval():
    p, rows, _ = package()
    reviewed = {**rows[0], 'status': 'REVIEWED', 'reviewer_id': 'reader_A', 'notes': 'Direct support checked',
        'evaluated_at': '2026-10-24T10:00:00Z', 'resource_grade': 3, 'evidence_grade': 1, 'snippet_grade': 0}
    other = {**reviewed, 'reviewer_id': 'reader_B', 'evidence_grade': 2}
    result = audit_judgments(p, [reviewed, other])
    assert len(result['conflicts']) == 1 and result['agreement'] == 0
    assert result['automatic_promotion'] is False
    with pytest.raises(ValueError):
        audit_judgments(p, [reviewed, reviewed])
    with pytest.raises(ValueError, match='authority'):
        audit_judgments(p, [{**reviewed, 'status': 'APPROVED'}])
    with pytest.raises(ValidationError):
        Judgment.model_validate({**reviewed, 'answerability': 'NO_DIRECT'})
    with pytest.raises(ValidationError):
        Judgment.model_validate({**reviewed, 'evaluated_at': '2026-10-24T10:00:00'})
    with pytest.raises(ValidationError):
        Judgment.model_validate({**reviewed, 'evidence_grade': True})
