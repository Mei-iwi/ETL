from copy import deepcopy

import pytest
from pydantic import ValidationError

from data_sync_etl.application.search_evaluation_snapshot import (
    EvaluationBag,
    SearchEvaluationSnapshot,
    capture_lexical_snapshot,
    replay_lexical_snapshot,
)
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import bm25
from tests.unit.test_search import searchable as searchable


def test_full_lexical_snapshot_replays_real_fixture_payload_without_dropping_titles_or_nonhits(searchable, tmp_path):
    c, _ = searchable
    documents = [EvaluationBag(bag_id=b.id, version_id=b.resource_version_id, resource_id='resource-1',
        generation=b.projection_generation, content_fingerprint=b.content_fingerprint,
        eligibility='verified_authorized_snapshot', lexical=b.lexical) for b in c.indexing.bags.rows.values()]
    snapshot = capture_lexical_snapshot(documents, c.search.dictionary, c.settings.search_expansion,
                                        provenance='isolated_fixture')
    parsed = SearchEvaluationSnapshot.model_validate_json(snapshot.model_dump_json())
    expected = bm25('page text', {b.bag_id: b.lexical for b in documents}, dictionary=c.search.dictionary,
                     policy=c.settings.search_expansion)
    assert replay_lexical_snapshot('page text', parsed) == expected
    assert parsed.bags[0].lexical == documents[0].lexical
    path = tmp_path / 'private-snapshot.json'
    path.write_text(parsed.model_dump_json(), encoding='utf-8')
    assert path.stat().st_size > 0
    payload = snapshot.model_dump()
    payload['bags'][0]['lexical']['length'] += 1
    with pytest.raises(ValidationError):
        SearchEvaluationSnapshot.model_validate(payload)
    payload = snapshot.model_dump()
    payload['policy']['title_boost'] += .1
    with pytest.raises(ValidationError):
        SearchEvaluationSnapshot.model_validate(payload)


def test_snapshot_identity_and_secret_fields_fail_closed(searchable):
    c, _ = searchable
    b = next(iter(c.indexing.bags.rows.values()))
    row = EvaluationBag(bag_id=b.id, version_id=b.resource_version_id, resource_id='resource-1',
        generation=b.projection_generation, content_fingerprint=b.content_fingerprint,
        eligibility='verified_authorized_snapshot', lexical=b.lexical)
    snapshot = capture_lexical_snapshot([row], c.search.dictionary, c.settings.search_expansion,
                                       provenance='isolated_fixture')
    payload = snapshot.model_dump()
    other = deepcopy(payload['bags'][0])
    other['bag_id'] = 'other'
    other['generation'] += 1
    payload['bags'].append(other)
    payload['checksum'] = digest({k: v for k, v in payload.items() if k != 'checksum'})
    with pytest.raises(ValidationError):
        SearchEvaluationSnapshot.model_validate(payload)
    payload = snapshot.model_dump()
    payload['credentials'] = {'password': 'forbidden-test-input'}
    with pytest.raises(ValidationError):
        SearchEvaluationSnapshot.model_validate(payload)
