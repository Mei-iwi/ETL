import json
from pathlib import Path

import pytest

from data_sync_etl.application.search_review import Judgment, ReviewQuery, check_splits
from scripts.metadata_acceptance_review import prepare


def test_metadata_review_has_no_fake_chunks_or_approved_labels(tmp_path, monkeypatch):
    source = Path('fixtures/search/multilingual_context.json').read_bytes()
    fixture = tmp_path / 'fixtures/search/multilingual_context.json'
    fixture.parent.mkdir(parents=True)
    fixture.write_bytes(source)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match='Private fresh'):
        prepare(Path('/outside-review'))
    output = tmp_path / '.test-tmp/review'
    assert prepare(output)['approved_judgments'] == 0
    rows = [json.loads(line) for line in (output / 'items.jsonl').read_text(encoding='utf-8').splitlines()]
    judgments = [json.loads(line) for line in (output / 'judgments.jsonl').read_text(encoding='utf-8').splitlines()]
    assert rows and all(row['chunk_id'] is row['content_unit_id'] is row['bag_id'] is None for row in rows)
    assert all(row['status'] == 'DRAFT' and row['resource_grade'] is None and row['evidence_grade'] is None for row in judgments)
    with pytest.raises(ValueError):
        prepare(output)


def test_family_cannot_cross_split_and_unjudged_is_not_negative():
    base = dict(text='Quang hợp', language='vi', category='paraphrase', intent_family_id='plant')
    with pytest.raises(ValueError, match='cross-split'):
        check_splits([ReviewQuery(id='one', split='validation', **base),
                      ReviewQuery(id='two', split='holdout', **{**base, 'text': 'Photosynthesis'})])
    row = Judgment(package_checksum='a' * 64, item_id='b' * 64)
    assert row.resource_grade is None and row.evidence_grade is None
    assert row.answerability == 'INSUFFICIENTLY_JUDGED' and row.status == 'DRAFT'
    with pytest.raises(ValueError, match='absence'):
        Judgment(package_checksum='a' * 64, item_id='b' * 64, answerability='NO_RELATED')
