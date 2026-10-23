"""Explicit lexical contract and exact legacy-schema recognition for migration."""

from copy import deepcopy

from data_sync_etl.adapters.mongo.schema import _validator_differences, check_schema, contracts
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import ANALYZER_VERSION, REPRESENTATION_VERSION

SCHEMA_VERSION = 'etl-bag-lexical-v2'
LEGACY_ANALYZER = 'pyvi-0.1.1-nfc-surface-compound-alnum-v1'
LEGACY_CONCEPT_PROPERTY_SHA256 = '7277fb50b9dcf2113e0c4fb6e6cdd3a2eeeecc5fe35bd461704843ecc631bc5d'


def is_legacy_concept_contract(validator, dimension):
    """Recognize one retired schema, never accept arbitrary extra properties."""
    candidate = deepcopy(validator)
    properties = candidate.get('$jsonSchema', {}).get('properties', {})
    legacy = properties.pop('concepts', None)
    return (digest(legacy) == LEGACY_CONCEPT_PROPERTY_SHA256
            and candidate == contracts(dimension)['content_bags'])


def lexical_contract(dimension, *, historical_dimensions=(), analyzer=ANALYZER_VERSION):
    validator = contracts(dimension, historical_dimensions=historical_dimensions)['content_bags']
    string = {'bsonType': 'string'}
    strings = {'bsonType': 'array', 'items': string}
    properties = {
        'version': {'enum': [REPRESENTATION_VERSION]},
        'analyzer': {'enum': [analyzer]},
        'fingerprint': {**string, 'pattern': '^[0-9a-f]{64}$'},
        'terms': {**strings, 'uniqueItems': True},
        'frequencies': {'bsonType': 'array', 'items': {'bsonType': 'int', 'minimum': 1}},
        'length': {'bsonType': 'int', 'minimum': 0},
        'title_terms': {**strings, 'uniqueItems': True},
        'title_frequencies': {'bsonType': 'array', 'items': {'bsonType': 'int', 'minimum': 1}},
        'passages': {'bsonType': 'array', 'items': strings},
        'title_tokens': strings,
    }
    # Optional so old bags stay readable for rollback; _verified requires an
    # exact source-derived payload before any search method considers them READY.
    validator['$jsonSchema']['properties']['lexical'] = {
        'bsonType': 'object', 'additionalProperties': False,
        'required': list(properties), 'properties': properties,
    }
    validator['$expr'] = {'$and': [validator['$expr'], {'$cond': [
        {'$eq': [{'$type': '$lexical'}, 'missing']}, True,
        {'$cond': [{'$and': [{'$isArray': '$lexical.terms'},
                            {'$isArray': '$lexical.frequencies'},
                            {'$isArray': '$lexical.title_terms'},
                            {'$isArray': '$lexical.title_frequencies'}]},
                   {'$and': [
                       {'$eq': ['$lexical.length', {'$sum': '$lexical.frequencies'}]},
                       {'$eq': [{'$size': '$lexical.terms'}, {'$size': '$lexical.frequencies'}]},
                       {'$eq': [{'$size': '$lexical.title_terms'}, {'$size': '$lexical.title_frequencies'}]},
                   ]}, False]},
    ]}]}
    return validator


def check_lexical_schema(database, *, dimension, audit_documents=False, historical_dimensions=()):
    return check_bag_schema(database, dimension=dimension,
                            bag_validator=lexical_contract(dimension, historical_dimensions=historical_dimensions),
                            audit_documents=audit_documents, historical_dimensions=historical_dimensions)


def check_bag_schema(database, *, dimension, bag_validator, audit_documents=False,
                     previous_bag_validator=None, historical_dimensions=()):
    report = check_schema(database, dimension=dimension, historical_dimensions=historical_dimensions)
    report['schema_version'] = SCHEMA_VERSION
    report['audit_documents'] = audit_documents
    catalog = {row['name']: row for row in database.list_collections()}
    installed = catalog.get('content_bags', {}).get('options', {}).get('validator', {})
    differences = _validator_differences(bag_validator, installed, 'content_bags')
    base_matches = installed == contracts(dimension, historical_dimensions=historical_dimensions)['content_bags']
    report['validator_mismatches'] = [row for row in report['validator_mismatches']
                                      if not row['path'].startswith('content_bags')]
    report['validator_mismatches'].extend(differences)
    if not differences and not base_matches:
        report['validators'] += 1  # The base checker saw the intentional extension.
    if audit_documents:
        validators = contracts(dimension, historical_dimensions=historical_dimensions)
        validators['content_bags'] = bag_validator
        report['legacy_invalid'] = {
            name: database[name].count_documents({'$nor': [validator, *(
                [previous_bag_validator] if name == 'content_bags' and previous_bag_validator else [])]})
            if name in catalog else 0
            for name, validator in validators.items()
        }
    report['schema_ready'] = not any(report[key] for key in (
        'missing_collections', 'validator_mismatches', 'validation_mismatches',
        'missing_indexes', 'incompatible_indexes', 'incompatible_collections', 'duplicate_indexes',
    )) and not any(report['legacy_invalid'].values())
    report['ready'] = report['schema_ready']
    return report
