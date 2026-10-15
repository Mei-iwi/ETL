"""Explicit, non-destructive Mongo foundation installer (never called at startup)."""

import argparse
import json
from dataclasses import dataclass

from pymongo import MongoClient
from pymongo.errors import PyMongoError

from data_sync_etl.config import Settings
from data_sync_etl.domain.core import DomainError

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


def contracts(dimension: int) -> dict[str, dict]:
    if not 1 <= dimension <= 4096:
        raise DomainError("Invalid embedding dimension")
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
            "unit_type": {"enum": ["CHAPTER", "LESSON", "SECTION"]},
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
    properties["indexed_chunks"] = {
        **{key: string(maximum=30, minimum=1) for key in
           ("resource_id", "resource_version_id", "content_unit_id", "index_run_id")},
        **{key: integer() for key in ("source_projection_generation", "chunk_sequence", "unit_chunk_sequence")},
        "page_from": integer(True), "page_to": integer(True),
        "review_status": string(maximum=30, minimum=1), "retrieval_eligible": {"bsonType": "bool"},
        "char_start": integer(minimum=0), "char_end": integer(), "text": string(minimum=1),
        "bow_tf": {"bsonType": "object", "additionalProperties": integer()},
        "bow_length": integer(minimum=0),
        "embedding_model": string(maximum=255, minimum=1),
        "embedding_version": string(maximum=128, minimum=1),
        "embedding_dimension": {**integer(), "maximum": 4096},
        "embedding": {"bsonType": "array", "minItems": 1, "maxItems": 4096,
                      "items": {"bsonType": "double", "minimum": -1.7976931348623157e308,
                                "maximum": 1.7976931348623157e308}},
        "content_fingerprint": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
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
        "indexed_chunks": list(properties["indexed_chunks"]),
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
    validators["indexed_chunks"]["$expr"] = {"$cond": [
        {"$and": [{"$isArray": "$embedding"}, {"$eq": [{"$type": "$text"}, "string"]},
                  {"$eq": [{"$type": "$char_start"}, "int"]},
                  {"$eq": [{"$type": "$char_end"}, "int"]}]},
        {"$and": [
            {"$eq": [{"$size": "$embedding"}, "$embedding_dimension"]},
            {"$gt": ["$char_end", "$char_start"]},
            {"$eq": [{"$strLenCP": "$text"}, {"$subtract": ["$char_end", "$char_start"]}]},
            {"$or": [{"$eq": ["$page_from", None]}, {"$eq": ["$page_to", None]},
                     {"$gte": ["$page_to", "$page_from"]}]},
            {"$anyElementTrue": [{"$map": {"input": "$embedding", "as": "v", "in": {"$ne": ["$$v", 0]}}}]},
        ]}, False]}
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
    "indexed_chunks": [
        Index("chunks_version_run_sequence", (("resource_version_id", 1), ("index_run_id", 1), ("chunk_sequence", 1)), True),
        Index("chunks_version_run_unit", (("resource_version_id", 1), ("index_run_id", 1), ("content_unit_id", 1))),
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


def initialize(database, *, dimension=256, apply=False, validation_level="strict") -> dict:
    """Preflight everything before DDL; DDL is resumable, not a multi-collection transaction."""
    if validation_level not in ("strict", "moderate"):
        raise DomainError("Unsupported validation level")
    validators = contracts(dimension)
    catalog = {row["name"]: row for row in database.list_collections()}
    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": "apply" if apply else "check",
        "collections": 0,
        "validators": 0,
        "indexes": 0,
        "legacy_invalid": {},
        "ready": True,
    }
    pending_indexes = {}
    for name, validator in validators.items():
        entry = catalog.get(name)
        if entry and entry.get("type") != "collection":
            raise DomainError(f"Incompatible Mongo collection: {name}")
        options = entry.get("options", {}) if entry else {}
        installed = options.get("validator", {})
        if installed and installed != validator:
            raise DomainError(f"Incompatible Mongo validator: {name}; review migration manually")
        ready_validator = (
            installed == validator
            and options.get("validationLevel", "strict") == validation_level
            and options.get("validationAction", "error") == "error"
        )
        report["collections"] += bool(entry)
        report["validators"] += bool(ready_validator)
        report["ready"] &= bool(entry and ready_validator)
        indexes = list(database[name].list_indexes()) if entry else []
        pending_indexes[name] = []
        for expected in INDEXES[name]:
            matches = [
                idx
                for idx in indexes
                if idx["name"] == expected.name or tuple(idx["key"].items()) == expected.keys
            ]
            if any(not compatible_index(idx, expected) for idx in matches):
                raise DomainError(f"Incompatible Mongo index: {name}/{expected.name}")
            report["indexes"] += bool(matches)
            if not matches:
                report["ready"] = False
                pending_indexes[name].append(expected)
                if entry and expected.unique:
                    group = {key: {"$ifNull": [f"${key}", None]} for key, _ in expected.keys}
                    duplicate = next(
                        database[name].aggregate(
                            [
                                {"$group": {"_id": group, "n": {"$sum": 1}}},
                                {"$match": {"n": {"$gt": 1}}},
                                {"$limit": 1},
                            ],
                            allowDiskUse=True,
                        ),
                        None,
                    )
                    if duplicate:
                        raise DomainError(f"Legacy duplicate keys: {name}/{expected.name}")
        invalid = database[name].count_documents({"$nor": [validator]}) if entry else 0
        report["legacy_invalid"][name] = invalid
        if invalid and validation_level == "strict":
            report["ready"] = False
        if apply and invalid and validation_level == "strict":
            raise DomainError(
                f"Legacy invalid documents: {name}; audit/backfill or explicitly use moderate"
            )
    if apply:
        for name, validator in validators.items():
            if name not in catalog:
                database.create_collection(
                    name,
                    validator=validator,
                    validationLevel=validation_level,
                    validationAction="error",
                )
            else:
                database.command(
                    "collMod",
                    name,
                    validator=validator,
                    validationLevel=validation_level,
                    validationAction="error",
                )
            for idx in pending_indexes[name]:
                database[name].create_index(list(idx.keys), name=idx.name, unique=idx.unique)
        checked = initialize(database, dimension=dimension, validation_level=validation_level)
        checked["mode"] = "apply"
        return checked
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Check/apply Mongo schemas additively without deleting data"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Read-only catalog and legacy audit")
    mode.add_argument("--apply", action="store_true", help="Explicit non-destructive DDL")
    parser.add_argument("--validation-level", choices=("strict", "moderate"), default="strict")
    args = parser.parse_args(argv)
    client = None
    try:
        settings = Settings()
        client = MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=3000, timeoutMS=30000)
        client.admin.command("ping")
        report = initialize(
            client[settings.mongo_database],
            dimension=settings.vector_dimension,
            apply=args.apply,
            validation_level=args.validation_level,
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
