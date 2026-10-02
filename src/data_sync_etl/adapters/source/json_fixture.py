import json
import sqlite3
import tempfile
from contextlib import closing
from functools import lru_cache
from heapq import nsmallest
from pathlib import Path

import ijson
from pydantic import ValidationError

from data_sync_etl.domain.core import DomainError
from data_sync_etl.ports.source import (
    Cursor,
    EducationLevel,
    GradeLevel,
    LearningResource,
    Provider,
    ResourceGradeLevelRelation,
    ResourceSubjectRelation,
    ResourceType,
    Role,
    SourceAggregate,
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

    def __init__(self, root):
        self.root = Path(root)
        self.lookup = lru_cache(maxsize=256)(self._lookup)

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
        # Disk-backed validation keeps RAM bounded and detects duplicates across ALL batches,
        # including rows behind the incremental cursor. This is scratch data, not the master DB.
        with tempfile.TemporaryDirectory(prefix="etl-source-keys-") as temporary:
            with closing(sqlite3.connect(str(Path(temporary) / "keys.db"))) as index:
                index.execute("PRAGMA cache_size=-512")
                index.execute(
                    "CREATE TABLE source_keys (scope TEXT, key TEXT, PRIMARY KEY(scope,key))"
                )
                for name in [stream, *dependencies[stream]]:
                    for row in self.records(name):
                        if name == "resource_subjects":
                            key = [row.resource_id, row.subject_id]
                        elif name == "resource_grades":
                            key = [row.resource_id, row.grade_level_id]
                        else:
                            key = row.id
                        try:
                            index.execute(
                                "INSERT INTO source_keys VALUES (?, ?)", (name, json.dumps(key))
                            )
                        except sqlite3.IntegrityError:
                            raise DomainError("Duplicate source identifier or relation") from None

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
        values = row.model_dump()
        timestamps = [row.updated_at]
        subjects, grades = [], []
        if stream == "resources":
            for prefix, parent in [
                ("provider_", self.get_provider(row.provider_id)),
                ("resource_type_", self.get_resource_type(row.resource_type_id)),
            ]:
                values.update({prefix + k: v for k, v in parent.model_dump().items() if k != "id"})
                timestamps.append(parent.updated_at)
            subjects = self.get_resource_subjects(row.id)
            grades = self.get_resource_grade_levels(row.id)
            timestamps += [r.updated_at for r in subjects + grades]
        elif stream == "users":
            parent = self.get_role(row.role_id)
            values.update({"role_" + k: v for k, v in parent.model_dump().items() if k != "id"})
            values["hashed_password"] = row.hashed_password.get_secret_value()
            timestamps.append(parent.updated_at)
        elif stream == "grades":
            parent = self.get_education_level(row.education_level_id)
            values = {"id": row.id, "education_level_id": row.education_level_id}
            values.update(
                {
                    "grade_" + k: v
                    for k, v in row.model_dump().items()
                    if k not in ("id", "education_level_id")
                }
            )
            values.update(
                {"education_" + k: v for k, v in parent.model_dump().items() if k != "id"}
            )
            timestamps.append(parent.updated_at)
        return SourceAggregate(
            cursor=Cursor(updated_at=max(timestamps), id=row.id),
            values=values,
            subjects=subjects,
            grades=grades,
        )

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

    def iter_learning_resources(self, cursor=None, batch_size=100):
        return self.batches("resources", cursor, batch_size)

    def iter_users(self, cursor=None, batch_size=100):
        return self.batches("users", cursor, batch_size)

    def iter_grade_levels(self, cursor=None, batch_size=100):
        return self.batches("grades", cursor, batch_size)

    def iter_subjects(self, cursor=None, batch_size=100):
        return self.batches("subjects", cursor, batch_size)
