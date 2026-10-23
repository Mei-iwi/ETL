import json
from copy import deepcopy
from pathlib import Path

from data_sync_etl.adapters.mongo.lexical_schema import lexical_contract
from data_sync_etl.adapters.mongo.meaning_schema import meaning_contract
from data_sync_etl.domain.indexing import digest


def test_meaning_schema_is_additive_and_fixes_vector_dimension():
    validator = meaning_contract(1024)
    base = lexical_contract(1024)
    added = validator['$jsonSchema']['properties'].pop('meanings')
    assert validator == base
    assert added['additionalProperties'] is False
    assert 'meanings' not in validator['$jsonSchema']['required']
    member = added['properties']['members']['items']
    assert member['properties']['embedding']['minItems'] == 1024
    assert member['properties']['embedding']['maxItems'] == 1024
    assert member['additionalProperties'] is False
    assert member['properties']['kind']['enum'] == ['title', 'text']
    threshold = added['properties']['group_threshold']
    assert threshold['minimum'] == 0 and threshold['exclusiveMinimum'] is True


def test_meaning_schema_does_not_mutate_lexical_contract():
    before = deepcopy(lexical_contract(3))
    meaning_contract(3)
    assert lexical_contract(3) == before


def test_search_extensions_preserve_upstream_historical_vector_guards():
    from data_sync_etl.adapters.mongo.schema import contracts

    base = contracts(1024, historical_dimensions=[384])['content_bags']
    for factory in (lexical_contract, meaning_contract):
        extended = factory(1024, historical_dimensions=[384])
        assert extended['$jsonSchema']['properties']['embedding'] == base['$jsonSchema']['properties']['embedding']
        assert extended['$expr']['$and'][0] == base['$expr']


def test_meaning_migration_manifest_matches_reference_validator():
    from data_sync_etl.adapters.mongo.lexical_schema import LEGACY_ANALYZER
    manifest = json.loads((Path(__file__).parents[2] / 'migrations_mongo/meanings_v1.json').read_text())
    assert manifest['validator_sha256'] == digest(meaning_contract(manifest['dimension'], analyzer=LEGACY_ANALYZER))
    assert manifest['collections_added'] == manifest['indexes_added'] == []
