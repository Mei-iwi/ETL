"""Explicit, non-destructive Mongo foundation installer (never called at startup)."""

import argparse
import json
from dataclasses import dataclass

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.mongo_documents import FALLBACK_BAG_TYPES, STRUCTURED_BAG_TYPES

SCHEMA_VERSION = "etl-mongo-foundation-v1"


def string(nullable=False, maximum=None, minimum=None):
    result = {"bsonType": ["string", "null"] if nullable else "string"}
    if maximum is not None:
        result["maxLength"] = maximum
    if minimum is not None:
        result["minLength"] = minimum
    return result


def integer(nullable=False, minimum=1):
    return {"bsonType": ["int", "null"] if nullable else "int", "minimum": minimum}


def strings(nullable=False):
    return {
        "bsonType": ["array", "null"] if nullable else "array",
        "items": string(minimum=1),
        "uniqueItems": True,
    }


def contracts(dimension: int, *, historical_dimensions=()) -> dict[str, dict]:
    if not 1 <= dimension <= 4096:
        raise DomainError("Invalid embedding dimension")
    if any(type(value) is not int or not 1 <= value <= 4096 or value == dimension for value in historical_dimensions):
        raise DomainError("Invalid historical embedding dimension")
    base = {
        "_id": string(maximum=30, minimum=1),
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": "date"},
    }
    projected = {
        "resource_version_id": string(maximum=30, minimum=1),
        "projection_generation": integer(),
    }
    author = {
        "bsonType": "object",
        "required": ["name"],
        "additionalProperties": False,
        "properties": {
            "name": {**string(minimum=1), "pattern": r"\S"},
            "organization": string(True),
            "role": string(True),
        },
    }
    properties = {
        "users": {
            "subject_id": string(True, 30, 1),
            "organization_name": string(True, 255),
            "address": string(True, 1000),
            "bio": string(True, 2000),
        },
        "learning_resources": {
            "description": string(True),
            "tags": strings(True),
            "authors": {"bsonType": ["array", "null"], "items": author},
        },
        "ocr_pages": {
            **projected,
            "_id": string(maximum=41, minimum=1),
            "job_id": string(maximum=30, minimum=1),
            "page_num": integer(),
            "raw_text": string(),
            "char_count": integer(minimum=0),
            "source_checksum": string(minimum=1),
            "confidence": {"bsonType": ["double", "int", "null"]},
            "engine_name": string(True),
            "engine_version": string(True),
        },
        "mongo_content_units": {
            **projected,
            "title": string(True, 500),
            "page_from": integer(True),
            "page_to": integer(True),
            "text": string(),
            "markdown": string(),
            "keywords": {**strings(True), "maxItems": 5},
            "content_unit_id": string(True, 30, 1),
            "page_result_id": string(True, 30, 1),
            "is_current": {"bsonType": "bool"},
        },
        "content_bags": {
            **projected,
            "unit_type": {"enum": [*STRUCTURED_BAG_TYPES, *FALLBACK_BAG_TYPES]},
            "title": string(True, 500),
            "keywords": {**strings(), "minItems": 1},
            "embedding_text": string(minimum=1),
            "embedding_model": {**string(maximum=255, minimum=1), "pattern": r"\S"},
            "embedding": {
                "bsonType": "array",
                "minItems": dimension,
                "maxItems": dimension,
                "items": {
                    "bsonType": ["double", "int", "long", "decimal"],
                    "minimum": -1.7976931348623157e308,
                    "maximum": 1.7976931348623157e308,
                },
            },
            "is_current": {"bsonType": "bool"},
        },
    }
    properties["content_bags"]["content_fingerprint"] = {
        "bsonType": "string",
        "pattern": "^[0-9a-f]{64}$",
    }
    required = {
        "users": [],
        "learning_resources": [],
        "ocr_pages": [
            "job_id",
            "resource_version_id",
            "page_num",
            "raw_text",
            "char_count",
            "source_checksum",
        ],
        "mongo_content_units": ["resource_version_id", "text", "markdown"],
        "content_bags": [
            "resource_version_id",
            "unit_type",
            "keywords",
            "embedding_text",
            "embedding",
            "embedding_model",
        ],
    }
    validators = {}
    for name, fields in properties.items():
        validators[name] = {
            "$jsonSchema": {
                "title": SCHEMA_VERSION,
                "bsonType": "object",
                "additionalProperties": False,
                "required": ["_id", "created_at", "updated_at", *required[name]],
                "properties": {**base, **fields},
            }
        }
    validators["ocr_pages"]["$expr"] = {
        "$cond": [
            {
                "$and": [
                    {"$eq": [{"$type": "$raw_text"}, "string"]},
                    {"$eq": [{"$type": "$job_id"}, "string"]},
                    {"$eq": [{"$type": "$page_num"}, "int"]},
                ]
            },
            {
                "$and": [
                    {"$eq": ["$char_count", {"$strLenCP": "$raw_text"}]},
                    {"$eq": ["$_id", {"$concat": ["$job_id", ":", {"$toString": "$page_num"}]}]},
                ]
            },
            False,
        ]
    }
    validators["mongo_content_units"]["$expr"] = {
        "$and": [
            {
                "$or": [
                    {"$eq": [{"$ifNull": ["$content_unit_id", None]}, None]},
                    {"$eq": ["$content_unit_id", "$_id"]},
                ]
            },
            {
                "$or": [
                    {"$eq": [{"$ifNull": ["$page_from", None]}, None]},
                    {"$eq": [{"$ifNull": ["$page_to", None]}, None]},
                    {"$gte": ["$page_to", "$page_from"]},
                ]
            },
        ]
    }
    # Guard expression types: legacy malformed arrays must audit as invalid, not throw.
    validators["content_bags"]["$expr"] = {
        "$cond": [
            {"$and": [{"$isArray": "$embedding"}, {"$isArray": "$keywords"}]},
            {
                "$and": [
                    {
                        "$anyElementTrue": [
                            {"$map": {"input": "$embedding", "as": "v", "in": {"$ne": ["$$v", 0]}}}
                        ]
                    },
                    {
                        "$allElementsTrue": [
                            {
                                "$map": {
                                    "input": "$embedding",
                                    "as": "v",
                                    "in": {
                                        "$and": [
                                            {"$isNumber": "$$v"},
                                            {"$gte": ["$$v", -1.7976931348623157e308]},
                                            {"$lte": ["$$v", 1.7976931348623157e308]},
                                        ]
                                    },
                                }
                            }
                        ]
                    },
                    {
                        "$eq": [
                            "$embedding_text",
                            {
                                "$reduce": {
                                    "input": "$keywords",
                                    "initialValue": "",
                                    "in": {
                                        "$cond": [
                                            {"$eq": [{"$type": "$$this"}, "string"]},
                                            {
                                                "$concat": [
                                                    "$$value",
                                                    {
                                                        "$cond": [
                                                            {"$eq": ["$$value", ""]},
                                                            "",
                                                            " | ",
                                                        ]
                                                    },
                                                    "$$this",
                                                ]
                                            },
                                            "<invalid-keyword>",
                                        ]
                                    },
                                }
                            },
                        ]
                    },
                ]
            },
            False,
        ]
    }
    if historical_dimensions:
        properties = validators['content_bags']['$jsonSchema']['properties']
        vector = properties['embedding']
        properties['embedding'] = {'oneOf': [
            {**vector, 'minItems': size, 'maxItems': size}
            for size in sorted({dimension, *historical_dimensions})]}
        # Historical dimensions can be retained only as inactive documents.
        # This remains inside the array type guard before evaluating $size.
        validators['content_bags']['$expr']['$cond'][1]['$and'].append({'$or': [
            {'$eq': [{'$size': '$embedding'}, dimension]}, {'$eq': ['$is_current', False]}]})
    return validators


@dataclass(frozen=True)
class Index:
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False


INDEXES = {
    "users": [],
    "learning_resources": [],
    "ocr_pages": [
        Index("ocr_job_page", (("job_id", 1), ("page_num", 1)), True),
        Index("ocr_version_page", (("resource_version_id", 1), ("page_num", 1))),
    ],
    "mongo_content_units": [
        Index(
            "units_version_current_page",
            (("resource_version_id", 1), ("is_current", 1), ("page_from", 1)),
        ),
        Index("units_keywords", (("keywords", 1),)),
    ],
    "content_bags": [
        Index("bags_version_type", (("resource_version_id", 1), ("unit_type", 1))),
        Index("bags_keywords", (("keywords", 1),)),
    ],
}


def compatible_index(existing: dict, expected: Index) -> bool:
    return (
        tuple(existing["key"].items()) == expected.keys
        and bool(existing.get("unique", False)) == expected.unique
        and not any(
            existing.get(k) for k in ("sparse", "partialFilterExpression", "collation", "hidden")
        )
        and "expireAfterSeconds" not in existing
    )


def inventory(database) -> dict:
    """Read-only inventory is independent of validator compatibility; never hide legacy extras."""
    expected = set(INDEXES)
    catalog = {row['name']: row for row in database.list_collections()}
    collections = []
    for name, entry in sorted(catalog.items()):
        options = entry.get('options', {})
        props = options.get('validator', {}).get('$jsonSchema', {}).get('properties', {})
        stats = database.command('collStats', name)
        installed_indexes = [_index_spec(idx) for idx in database[name].list_indexes()]
        collections.append({
            'name': name, 'documents': database[name].count_documents({}),
            'size_bytes': stats.get('size', 0),
            'indexes': [idx['name'] for idx in installed_indexes],
            'index_specifications': installed_indexes,
            'validation_level': options.get('validationLevel', 'strict'),
            'validation_action': options.get('validationAction', 'error'),
            'embedding_validator_min_items': props.get('embedding', {}).get('minItems'),
            'embedding_validator_max_items': props.get('embedding', {}).get('maxItems'),
        })
        if name == 'content_bags':
            collections[-1]['embedding_lengths'] = list(database[name].aggregate([
                {'$group': {'_id': {'$cond': [{'$isArray': '$embedding'},
                                             {'$size': '$embedding'}, 'NOT_ARRAY']},
                            'documents': {'$sum': 1}}}, {'$sort': {'_id': 1}},
            ]))
    extras = sorted(set(catalog) - expected)
    missing = sorted(expected - set(catalog))
    return {'database': database.name, 'mode': 'inventory', 'expected': sorted(expected),
            'actual': collections, 'extra_collections': extras, 'missing_collections': missing,
            'warnings': (['EXTRA_COLLECTION_PRESENT'] if extras else [])
                        + (['MISSING_EXPECTED_COLLECTIONS'] if missing else [])}


def _validator_differences(expected, actual, path):
    """Only schema metadata is inspected; document values never enter this report."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        result = []
        for key in sorted(expected.keys() | actual.keys()):
            child = path if key in ("$jsonSchema", "properties") else f"{path}.{key}"
            if key not in expected or key not in actual:
                result.append({"path": child, "expected": expected.get(key),
                               "actual": actual.get(key)})
            else:
                result.extend(_validator_differences(expected[key], actual[key], child))
        return result
    if expected != actual:
        return [{"path": path, "expected": expected, "actual": actual}]
    return []


def _index_spec(index):
    return {key: value for key, value in index.items()
            if key in ("name", "key", "unique", "sparse", "partialFilterExpression",
                       "collation", "hidden", "expireAfterSeconds")}


def check_schema(database, *, dimension, validation_level="strict", audit_documents=False, historical_dimensions=()):
    """Shared read-only guard. HTTP/worker checks never scan stored documents."""
    if validation_level not in ("strict", "moderate"):
        raise DomainError("Unsupported validation level")
    validators = contracts(dimension, historical_dimensions=historical_dimensions)
    catalog = {row["name"]: row for row in database.list_collections()}
    report = {
        "database": database.name, "schema_version": SCHEMA_VERSION, "mode": "check",
        "effective_expected_dimension": dimension, "audit_documents": audit_documents,
        "collections": 0, "validators": 0, "indexes": 0, "legacy_invalid": {},
        "missing_collections": [], "validator_mismatches": [], "validation_mismatches": [],
        "missing_indexes": [], "incompatible_indexes": [], "incompatible_collections": [],
        "duplicate_indexes": [], "extra_collections": sorted(set(catalog) - set(validators)),
        "warnings": ["EXTRA_COLLECTION_PRESENT"] if set(catalog) - set(validators) else [],
        "installed_indexes": {}, "schema_ready": True,
    }
    for name, validator in validators.items():
        entry = catalog.get(name)
        if not entry:
            report["missing_collections"].append(name)
        elif entry.get("type") != "collection":
            report["incompatible_collections"].append(name)
            continue
        report["collections"] += bool(entry)
        options = entry.get("options", {}) if entry else {}
        installed = options.get("validator", {})
        differences = _validator_differences(validator, installed, name)
        report["validator_mismatches"].extend(differences)
        level = options.get("validationLevel", "strict")
        action = options.get("validationAction", "error")
        for field, expected, actual in (
            ("validationLevel", validation_level, level), ("validationAction", "error", action)
        ):
            if actual != expected:
                report["validation_mismatches"].append({
                    "path": f"{name}.{field}", "expected": expected, "actual": actual})
        report["validators"] += bool(entry and not differences
                                     and level == validation_level and action == "error")
        indexes = list(database[name].list_indexes()) if entry else []
        report["installed_indexes"][name] = [_index_spec(i) for i in indexes]
        for expected in INDEXES[name]:
            matches = [idx for idx in indexes if idx["name"] == expected.name
                       or tuple(idx["key"].items()) == expected.keys]
            item = {"collection": name, "name": expected.name,
                    "expected": {"key": list(expected.keys), "unique": expected.unique}}
            if any(not compatible_index(idx, expected) for idx in matches):
                report["incompatible_indexes"].append({
                    **item, "actual": [_index_spec(i) for i in matches]})
            elif matches:
                report["indexes"] += 1
            else:
                report["missing_indexes"].append(item)
                if audit_documents and entry and expected.unique:
                    group = {key: {"$ifNull": [f"${key}", None]} for key, _ in expected.keys}
                    duplicate = next(database[name].aggregate([
                        {"$group": {"_id": group, "n": {"$sum": 1}}},
                        {"$match": {"n": {"$gt": 1}}}, {"$limit": 1},
                    ], allowDiskUse=True), None)
                    if duplicate:
                        report["duplicate_indexes"].append(item)
        if audit_documents:
            report["legacy_invalid"][name] = (
                database[name].count_documents({"$nor": [validator]}) if entry else 0)
    report["schema_ready"] = not any(report[key] for key in (
        "missing_collections", "validator_mismatches", "validation_mismatches",
        "missing_indexes", "incompatible_indexes", "incompatible_collections", "duplicate_indexes",
    ))
    if audit_documents and validation_level == "strict" and any(report["legacy_invalid"].values()):
        report["schema_ready"] = False
    report["ready"] = report["schema_ready"]
    return report


def initialize(database, *, dimension, apply=False, validation_level="strict", historical_dimensions=()) -> dict:
    """Audit all collections before explicit additive DDL; never migrate incompatible schemas."""
    report = check_schema(database, dimension=dimension, validation_level=validation_level,
                          audit_documents=True, historical_dimensions=historical_dimensions)
    if not apply:
        return report
    validators = contracts(dimension, historical_dimensions=historical_dimensions)
    catalog = {row["name"]: row for row in database.list_collections()}
    # Preflight ALL collections and indexes before the first write.
    if report["incompatible_collections"]:
        raise DomainError("Incompatible Mongo collection; review migration manually")
    if any(catalog.get(name, {}).get("options", {}).get("validator")
           and catalog[name]["options"]["validator"] != validator
           for name, validator in validators.items()):
        raise DomainError("Incompatible Mongo validator; review migration manually")
    if any(catalog.get(item["path"].split(".")[0], {}).get("options", {}).get("validator")
           for item in report["validation_mismatches"]):
        raise DomainError("Incompatible Mongo validation options; review migration manually")
    if report["incompatible_indexes"]:
        raise DomainError("Incompatible Mongo index; review migration manually")
    if report["duplicate_indexes"]:
        raise DomainError("Legacy duplicate keys; review migration manually")
    if validation_level == "strict" and any(report["legacy_invalid"].values()):
        raise DomainError("Legacy invalid documents; audit/backfill or explicitly use moderate")
    for name, validator in validators.items():
        if name not in catalog:
            database.create_collection(name, validator=validator, validationLevel=validation_level,
                                       validationAction="error")
        elif not catalog[name].get("options", {}).get("validator"):
            database.command("collMod", name, validator=validator,
                             validationLevel=validation_level, validationAction="error")
    for item in report["missing_indexes"]:
        idx = next(i for i in INDEXES[item["collection"]] if i.name == item["name"])
        database[item["collection"]].create_index(list(idx.keys), name=idx.name, unique=idx.unique)
    checked = check_schema(database, dimension=dimension, validation_level=validation_level,
                           audit_documents=True, historical_dimensions=historical_dimensions)
    checked["mode"] = "apply"
    return checked


def bootstrap_empty(database, *, dimension) -> dict:
    """Compose first-run only: never repair an existing incompatible database."""
    if next(database.list_collections(), None) is not None:
        report = check_schema(database, dimension=dimension, audit_documents=True)
        if not report['schema_ready']:
            raise DomainError(
                'Existing Mongo schema is not ready; run --check, back up and obtain '
                'approval before --apply. No schema changed.'
            )
    else:
        report = initialize(database, dimension=dimension, apply=True)
    report['mode'] = 'bootstrap-empty'
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check/apply Mongo schemas additively without deleting data"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only catalog and legacy audit")
    mode.add_argument("--apply", action="store_true", help="Explicit non-destructive DDL")
    mode.add_argument("--bootstrap-empty", action="store_true",
                      help="Initialize an empty DB; otherwise check only, never repair existing schemas")
    mode.add_argument("--inventory", action="store_true", help="Read-only collection inventory, including legacy extras")
    parser.add_argument("--validation-level", choices=("strict", "moderate"), default="strict")
    args = parser.parse_args(argv)
    client = None
    try:
        settings = Settings()
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000, timeoutMS=30000)
        client.admin.command("ping")
        if args.inventory:
            print(json.dumps(inventory(client[settings.mongo_database]), ensure_ascii=True))
            return 0
        if args.bootstrap_empty:
            if args.validation_level != 'strict':
                raise DomainError('Bootstrap requires strict validation')
            report = bootstrap_empty(client[settings.mongo_database],
                                     dimension=settings.index_embedding_dimension)
            print(json.dumps(report, ensure_ascii=True))
            return 0 if report['schema_ready'] else 1
        report = initialize(
            client[settings.mongo_database],
            dimension=settings.index_embedding_dimension,
            apply=args.apply,
            validation_level=args.validation_level,
            historical_dimensions=settings.index_historical_embedding_dimensions,
        )
        print(json.dumps(report, ensure_ascii=True))
        return 0 if report["ready"] else 1
    except DomainError as exc:
        print(str(exc))
        return 2
    except (PyMongoError, ValueError):
        print(
            "Mongo initialization failed; check connectivity, settings and privileges. No data deleted."
        )
        return 2
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
