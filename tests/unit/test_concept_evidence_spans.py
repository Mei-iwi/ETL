from copy import deepcopy

from data_sync_etl.domain.corpus_concepts import discover, valid_relation
from data_sync_etl.domain.indexing import digest
from tests.unit.test_dynamic_concept_dictionary import resource


def test_explicit_relation_span_matches_actual_normalized_source_and_is_review_bound():
    text = 'Unrelated paragraph.\nvi: mạng tinh thể | en: zircon lattice'
    result = discover(resource(fields={'title': text}))
    candidate = result['relations'][0]
    evidence = candidate['evidence'][0]
    assert digest(text[evidence['char_start']:evidence['char_end']]) == evidence['span_fingerprint']
    assert valid_relation(candidate, result['fingerprint'])
    for field, value in [('char_start', -1), ('char_end', 999999), ('span_fingerprint', '0' * 64)]:
        changed = deepcopy(candidate)
        changed['evidence'][0][field] = value
        assert not valid_relation(changed, result['fingerprint'])


def test_plain_cooccurrence_never_gets_relation_evidence():
    assert not discover(resource(fields={'title': 'A mineral and another mineral occur together.'}))['relations']
