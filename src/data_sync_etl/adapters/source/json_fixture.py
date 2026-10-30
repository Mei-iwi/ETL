from functools import lru_cache
from heapq import nsmallest
from pathlib import Path

import ijson
from pydantic import ValidationError

from data_sync_etl.adapters.source.aggregate import build_aggregate
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.source_ids import SourceLocator
from data_sync_etl.ports.source import (
    EducationLevel,
    GradeLevel,
    LearningResource,
    Provider,
    ResourceGradeLevelRelation,
    ResourceSubjectRelation,
    ResourceType,
    Role,
    Subject,
    User,
)

MODELS = {
    "providers": Provider,
    "resource_types": ResourceType,
    "resources": LearningResource,
    "roles": Role,
    "users": User,
    "education_levels": EducationLevel,
    "grades": GradeLevel,
    "subjects": Subject,
    "resource_subjects": ResourceSubjectRelation,
    "resource_grades": ResourceGradeLevelRelation,
}


class JsonFixtureSourceAdapter:
    """Bounded-memory reference adapter. Files must remain immutable during a sync run."""

    source_backend = 'json-fixture'
    source_namespace = 'fixture:source'

    def __init__(self, root):
        self.root = Path(root)
        self.lookup = lru_cache(maxsize=256)(self._lookup)

    def locator(self, entity_type: str, source_record_id: str) -> SourceLocator:
        return SourceLocator(
            backend=self.source_backend,
            namespace=self.source_namespace,
            entity_type=entity_type,
            source_record_id=source_record_id,
        )

    def records(self, name):
        try:
            with (self.root / f"{name}.json").open("rb") as file:
                # The fixture contract is a top-level JSON array, not a silent empty object.
                while (first := file.read(1)) and first.isspace():
                    pass
                if first != b"[":
                    raise DomainError("Source fixture must contain a JSON array")
                file.seek(0)
                for value in ijson.items(file, "item"):
                    yield MODELS[name].model_validate(value)
        except (OSError, ValidationError, ijson.JSONError):
            raise DomainError("Invalid or unreadable source fixture") from None

    def validate_keys(self, stream):
        dependencies = {
            "resources": ["providers", "resource_types", "resource_subjects", "resource_grades"],
            "users": ["roles"],
            "grades": ["education_levels"],
            "subjects": [],
        }
        # JSON fixtures are a bounded local-test adapter. Keep validation in memory
        # so it never creates a helper database or table outside the MASTER schema.
        seen = {name: set() for name in [stream, *dependencies[stream]]}
        total = 0
        for name in seen:
            for row in self.records(name):
                if name == "resource_subjects":
                    key = (row.resource_id, row.subject_id)
                elif name == "resource_grades":
                    key = (row.resource_id, row.grade_level_id)
                else:
                    key = row.id
                total += 1
                if total > 250_000:
                    raise DomainError("Fixture validation exceeds in-memory safety limit")
                if key in seen[name]:
                    raise DomainError("Duplicate source identifier or relation")
                seen[name].add(key)

    def _lookup(self, name, source_id):
        for row in self.records(name):
            if row.id == source_id:
                return row
        raise DomainError("Missing source dependency")

    def get_provider(self, provider_id):
        return self.lookup("providers", provider_id)

    def get_resource_type(self, resource_type_id):
        return self.lookup("resource_types", resource_type_id)

    def get_role(self, role_id):
        return self.lookup("roles", role_id)

    def get_education_level(self, education_level_id):
        return self.lookup("education_levels", education_level_id)

    def get_resource_subjects(self, resource_id):
        return [r for r in self.records("resource_subjects") if r.resource_id == resource_id]

    def get_resource_grade_levels(self, resource_id):
        return [r for r in self.records("resource_grades") if r.resource_id == resource_id]

    def aggregate(self, stream, row):
        return build_aggregate(self, stream, row)

    def batches(self, stream, cursor=None, batch_size=100):
        if stream not in {"resources", "users", "grades", "subjects"} or batch_size < 1:
            raise DomainError("Invalid source stream or batch size")
        self.lookup.cache_clear()
        self.validate_keys(stream)
        while True:

            def eligible(cursor=cursor):
                for row in self.records(stream):
                    aggregate = self.aggregate(stream, row)
                    if cursor is None or aggregate.cursor.key() > cursor.key():
                        yield aggregate

            batch = nsmallest(batch_size, eligible(), key=lambda a: a.cursor.key())
            if not batch:
                return
            if len({a.cursor.id for a in batch}) != len(batch):
                raise DomainError("Duplicate source ID in batch")
            yield batch
            cursor = batch[-1].cursor

    def selected_batches(self, stream, record_ids, batch_size=100):
        wanted = set(record_ids)
        batch = []
        for row in self.records(stream):
            if row.id in wanted:
                batch.append(self.aggregate(stream, row))
                if len(batch) == batch_size:
                    yield batch
                    batch = []
        if batch:
            yield batch

    def iter_learning_resources(self, cursor=None, batch_size=100):
        return self.batches("resources", cursor, batch_size)

    def iter_users(self, cursor=None, batch_size=100):
        return self.batches("users", cursor, batch_size)

    def iter_grade_levels(self, cursor=None, batch_size=100):
        return self.batches("grades", cursor, batch_size)

    def iter_subjects(self, cursor=None, batch_size=100):
        return self.batches("subjects", cursor, batch_size)
