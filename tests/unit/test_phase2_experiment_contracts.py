import json
from pathlib import Path

from scripts.phase2_search_experiments import rendered_grade


def test_rendered_anchor_grading_is_bound_to_resource_and_existing_annotations():
    annotations = [{'grade': 3, 'resource_ids': ['allowed'], 'anchors': ['muối hòa tan']}]
    assert rendered_grade('MUỐI HÒA TAN trong nước', 'allowed', annotations) == 3
    assert rendered_grade('muối hòa tan', 'allowed', annotations) == 3
    assert rendered_grade('muối hòa tan', 'different-resource', annotations) == 0
    assert rendered_grade('Đoạn bị cắt trước dẫn chứng', 'allowed', annotations) == 0
    assert annotations[0]['grade'] == 3


def test_new_query_suite_is_draft_references_existing_public_docs_and_does_not_approve_terms():
    fixture = json.loads(Path('fixtures/search/phase2_queries.json').read_text(encoding='utf-8'))
    base = json.loads(Path(fixture['base_fixture']).read_text(encoding='utf-8'))
    ids = {d['id'] for d in base['documents']}
    assert fixture['label_status'].startswith('DRAFT_')
    assert len({q['id'] for q in fixture['queries']}) == len(fixture['queries'])
    assert {q['language'] for q in fixture['queries']} >= {'vi', 'en', 'fr', 'vi-en', 'vi-fr', 'en-fr', 'vi-en-fr'}
    for query in fixture['queries']:
        assert set(query['qrels']) <= ids
        assert all(grade in (1, 2, 3) for grade in query['qrels'].values())
    assert any(not any(grade >= 2 for grade in q['qrels'].values()) for q in fixture['queries'])
    pending = json.loads(Path('fixtures/search/terminology-draft.json').read_text(encoding='utf-8'))
    assert all(c['review_status'] == 'pending' and all(t['review_status'] == 'pending' for t in c['terms'])
               for c in pending['concepts'])
