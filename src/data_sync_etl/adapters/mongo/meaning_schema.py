"""Additive meaning-bag validator; no collections/indexes or automatic DDL."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

from bson import json_util
from pymongo import MongoClient
from pymongo.errors import PyMongoError

from data_sync_etl.adapters.mongo.lexical_schema import check_bag_schema, lexical_contract
from data_sync_etl.adapters.mongo.schema import contracts
from data_sync_etl.config import Settings
from data_sync_etl.domain.indexing import digest
from data_sync_etl.domain.lexical import ANALYZER_VERSION
from data_sync_etl.domain.meaning_bags import MeaningBag


def meaning_contract(dimension, *, historical_dimensions=(), analyzer=ANALYZER_VERSION):
    schema = MeaningBag.model_json_schema()
    definitions = schema.pop('$defs', {})

    def convert(node):
        if '$ref' in node:
            return convert(definitions[node['$ref'].split('/')[-1]])
        result = {}
        types = {'object': 'object', 'array': 'array', 'string': 'string', 'integer': 'int', 'number': 'double'}
        for key, value in node.items():
            if key == 'type':
                result['bsonType'] = types[value]
            elif key == 'const':
                result['enum'] = [value]
            elif key == 'properties':
                result[key] = {name: convert(prop) for name, prop in value.items()}
            elif key == 'items':
                result[key] = convert(value)
            elif key == 'exclusiveMinimum':
                result.update(minimum=value, exclusiveMinimum=True)
            elif key in ('required', 'additionalProperties', 'enum', 'minimum', 'maximum',
                         'minLength', 'maxLength', 'minItems', 'maxItems', 'pattern'):
                result[key] = value
        if 'embedding' in result.get('properties', {}):
            result['properties']['embedding'].update(minItems=dimension, maxItems=dimension)
        return result

    validator = deepcopy(lexical_contract(dimension, historical_dimensions=historical_dimensions, analyzer=analyzer))
    validator['$jsonSchema']['properties']['meanings'] = convert(schema)
    validator['$jsonSchema']['properties']['meanings']['properties']['dimension']['enum'] = [dimension]
    return validator


def check_meanings_schema(database, *, dimension, audit_documents=False, historical_dimensions=()):
    result = check_bag_schema(database, dimension=dimension,
                             bag_validator=meaning_contract(dimension, historical_dimensions=historical_dimensions),
                             audit_documents=audit_documents, historical_dimensions=historical_dimensions)
    result['schema_version'] = 'content-bag-meanings-v1'
    return result


def install(database, *, dimension, apply=False):
    installed = next(database.list_collections(filter={'name': 'content_bags'}))['options']['validator']
    allowed = (contracts(dimension)['content_bags'], lexical_contract(dimension), meaning_contract(dimension))
    if installed not in allowed:
        raise ValueError('Unknown validator; inspect target before applying meaning extension')
    report = check_bag_schema(database, dimension=dimension, bag_validator=installed, audit_documents=True)
    if not report['ready']:
        raise ValueError('Existing schema/documents are not ready')
    if apply and installed != allowed[-1]:
        database.command('collMod', 'content_bags', validator=allowed[-1],
                         validationLevel='strict', validationAction='error')
    return check_meanings_schema(database, dimension=dimension, audit_documents=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    settings = Settings()
    try:
        with MongoClient(settings.mongo_uri, tz_aware=True, serverSelectionTimeoutMS=5000) as client:
            database = client[settings.mongo_database]
            if args.apply:
                if args.backup is None or args.backup.exists():
                    parser.error('--apply requires a new --backup path')
                install(database, dimension=settings.index_embedding_dimension)
                options = next(database.list_collections(filter={'name': 'content_bags'}))['options']
                args.backup.parent.mkdir(parents=True, exist_ok=True)
                with args.backup.open('x', encoding='utf-8') as output:
                    output.write(json_util.dumps({'target': digest([settings.mongo_uri, database.name]), 'options': options}))
            report = install(database, dimension=settings.index_embedding_dimension, apply=args.apply)
            print(json.dumps(report, ensure_ascii=True, default=str, indent=2))
    except (PyMongoError, ValueError, StopIteration):
        print('Meaning schema unavailable or incompatible; inspect connectivity, target and existing schema.')
        return 2
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
