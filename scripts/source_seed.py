"""Explicit fake-data seed for an EMPTY source database; never runs during container startup."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import MetaData, create_engine, select

from data_sync_etl.config import Settings

SEED_TABLES = (
    ("education_levels", "education_levels"),
    ("grades", "grade_levels"),
    ("subjects", "subjects"),
    ("providers", "providers"),
    ("resource_types", "resource_types"),
    ("roles", "roles"),
    ("users", "users"),
    ("resources", "learning_resources"),
    ("resource_subjects", "resource_subjects"),
    ("resource_grades", "resource_grade_levels"),
)


def seed_demo(connection, root):
    schema = MetaData()
    schema.reflect(bind=connection, only=[table for _, table in SEED_TABLES])
    for _, name in SEED_TABLES:
        if connection.execute(select(schema.tables[name]).limit(1)).first() is not None:
            raise RuntimeError(
                "Demo seed requires empty source metadata; existing data was not changed"
            )
    fixtures = {
        file: json.loads((Path(root) / f"{file}.json").read_text(encoding="utf-8"))
        for file, _ in SEED_TABLES
    }
    for file, name in SEED_TABLES:
        table = schema.tables[name]
        for raw in fixtures[file]:
            raw = dict(raw)
            if name == "providers":
                raw["provider_type"] = raw.pop("type")
            if name == "learning_resources" and raw.get("created_by") is None:
                raw["created_by"] = fixtures["users"][0]["id"]  # Fake author for this demo only.
            values = {key: value for key, value in raw.items() if key in table.c}
            for key, value in values.items():
                if key.endswith("_at") and isinstance(value, str):
                    values[key] = datetime.fromisoformat(value.replace("Z", "+00:00"))
            connection.execute(table.insert().values(**values))
    # Historical INSERT timestamps stay intact. Relation changes legitimately touch
    # resource markers; never rewind updated_at after those changes.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-empty-demo-source", action="store_true", required=True)
    parser.add_argument("--fixture-root", type=Path, default=Path("fixtures/source"))
    args = parser.parse_args()
    url = Settings().source_migration_database_url
    if not url:
        parser.error("SOURCE_MIGRATION_DATABASE_URL is required")
    engine = create_engine(url, hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as connection:
            seed_demo(connection, args.fixture_root)
        print("Fake source metadata seeded; no passwords or personal data emitted")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
