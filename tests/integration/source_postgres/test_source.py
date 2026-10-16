import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from data_sync_etl.adapters.source.json_fixture import JsonFixtureSourceAdapter
from data_sync_etl.adapters.source.postgresql import PostgreSQLSourceAdapter
from data_sync_etl.application.sync import MasterSync
from data_sync_etl.domain.core import DomainError
from data_sync_etl.main import create_app

from .conftest import migrate_source

pytestmark = pytest.mark.postgres
COUNTS = {
    "id_counters": 3,
    "education_levels": 9,
    "grade_levels": 13,
    "subjects": 10,
    "resource_grade_levels": 2,
    "resource_subjects": 3,
    "providers": 8,
    "resource_types": 8,
    "roles": 9,
    "users": 10,
    "learning_resources": 12,
    "resource_versions": 20,
    "content_units": 13,
    "ocr_jobs": 12,
    "ocr_page_tasks": 13,
}
ENUMS = {
    "provider_type": "PUBLISHER SCHOOL ORGANIZATION INTERNAL",
    "publication_status": "DRAFT PUBLISHED ARCHIVED",
    "lifecycle_status": "UPLOADED PENDING_PRE_REVIEW QUEUED PROCESSING READY_FOR_REVIEW APPROVED REJECTED FAILED CANCELLED",
    "ocr_job_status": "QUEUED PROCESSING POSTPROCESSING COMPLETED FAILED CANCELLED",
    "ocr_page_task_status": "PENDING PROCESSING COMPLETED FAILED CANCELLED",
    "content_unit_type": "PART CHAPTER LESSON SECTION SUBSECTION",
    "content_review_status": "PENDING APPROVED REJECTED",
}


def execute(source, sql, **values):
    with source.engine.begin() as connection:
        return connection.execute(text(sql), values)


def use_source(container, source):
    container.source = source.adapter
    container.sync = MasterSync(source.adapter, container.uow, batch_size=1)


def test_erd_metadata_and_revision_replay(source_database):
    s = source_database
    with s.engine.connect() as connection:
        inspector = inspect(connection)
        contract = json.loads(
            Path(__file__).with_name("erd_contract.json").read_text(encoding="utf-8")
        )
        assert set(inspector.get_table_names()) - {"alembic_source_version"} == set(COUNTS)
        assert sum(len(inspector.get_columns(t)) for t in COUNTS) == sum(COUNTS.values()) == 145
        for table, count in COUNTS.items():
            columns = inspector.get_columns(table)
            assert len(columns) == count
            assert {col["name"] for col in columns} == set(contract[table])
            foreign_keys = {
                fk["constrained_columns"][0]: (fk["referred_table"], fk["referred_columns"][0])
                for fk in inspector.get_foreign_keys(table)
            }
            unique_columns = {
                tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)
            }
            primary = inspector.get_pk_constraint(table)["constrained_columns"]
            for col in columns:
                kind, length, nullable, fk, unique = contract[table][col["name"]]
                assert col["nullable"] == nullable, (table, col["name"])
                if kind in {"VARCHAR", "TEXT", "SMALLINT", "INTEGER", "BOOLEAN"}:
                    assert str(col["type"]).split("(")[0] == kind
                elif kind == "TIMESTAMPTZ":
                    assert str(col["type"]) == "TIMESTAMP" and col["type"].timezone
                else:
                    assert col["type"].name == kind
                if length:
                    assert col["type"].length == length
                if fk:
                    assert foreign_keys[col["name"]] == tuple(fk)
                else:
                    assert col["name"] not in foreign_keys
                if unique:
                    assert (col["name"],) in unique_columns or primary == [col["name"]]
                if col["name"].endswith("_at"):
                    assert col["type"].timezone
                if col["name"].endswith("_id") or col["name"] == "id":
                    expected = (
                        100
                        if col["name"] == "worker_id"
                        else 24
                        if table in {"grade_levels", "subjects"} and col["name"] == "mongo_id"
                        else 30
                    )
                    assert col["type"].length == expected
            if "id" in {col["name"] for col in columns}:
                assert inspector.get_pk_constraint(table)["constrained_columns"] == ["id"]
        assert inspector.get_pk_constraint("id_counters")["constrained_columns"] == [
            "prefix",
            "period",
        ]
        assert inspector.get_pk_constraint("resource_subjects")["constrained_columns"] == [
            "resource_id",
            "subject_id",
        ]
        assert inspector.get_pk_constraint("resource_grade_levels")["constrained_columns"] == [
            "resource_id",
            "grade_level_id",
        ]
        assert {
            col["name"] for col in inspector.get_columns("learning_resources") if col["nullable"]
        } == {"mongo_id", "provider_id", "title", "deleted_at", "deleted_by"}
        assert "updated_at" not in {
            col["name"] for col in inspector.get_columns("resource_subjects")
        }
        assert ["resource_version_id"] in [
            u["column_names"] for u in inspector.get_unique_constraints("ocr_jobs")
        ]
        assert {e["name"]: e["labels"] for e in inspector.get_enums(schema=s.schema)} == {
            name: values.split() for name, values in ENUMS.items()
        }
        assert len(inspector.get_foreign_keys("resource_versions")) == 7
        assert len(inspector.get_foreign_keys("ocr_page_tasks")) == 1
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal"
                )
            )
            == 26
        )
    migrate_source(s.engine)
    migrate_source(s.engine, "s0001", downgrade=True)
    migrate_source(s.engine)
    migrate_source(s.engine, "base", downgrade=True)
    migrate_source(s.engine)


def test_json_postgresql_equivalence_and_idempotency(source, pg_container):
    # Source requires an author. Align both fake seeds; adapter itself never fabricates one.
    path = pg_container.source.root / "resources.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    with source.engine.connect() as connection:
        seed_markers = dict(
            connection.execute(text("SELECT id,updated_at FROM learning_resources")).all()
        )
    for row in rows:
        row["created_by"] = "user-1"
        # Same fixture business records, including the legitimate relation-touch markers.
        row["updated_at"] = seed_markers[row["id"]].isoformat()
    path.write_text(json.dumps(rows), encoding="utf-8")
    for file in ("resource_subjects.json", "resource_grades.json"):
        relation_path = path.with_name(file)
        relations = json.loads(relation_path.read_text(encoding="utf-8"))
        for relation in relations:
            relation["updated_at"] = seed_markers[relation["resource_id"]].isoformat()
        relation_path.write_text(json.dumps(relations), encoding="utf-8")
    json_source = JsonFixtureSourceAdapter(pg_container.source.root)
    for stream in ("grades", "subjects", "users", "resources"):
        expected = [a.model_dump() for batch in json_source.batches(stream, None, 2) for a in batch]
        actual = [
            a.model_dump() for batch in source.adapter.batches(stream, None, 1) for a in batch
        ]
        # PostgreSQL explicitly supplies NULL; old JSON fixtures omit mongo_id.
        # Compare business values while testing presence-awareness separately.
        if stream in ('users', 'resources', 'grades'):
            for record in actual + expected:
                record['values'].setdefault('grade_mongo_id' if stream == 'grades' else 'mongo_id', None)
        assert actual == expected
    use_source(pg_container, source)
    first = pg_container.sync.run(full=True)
    assert first["resources"]["inserted"] == 3
    repeated = pg_container.sync.run(full=True)
    assert all(row["updated"] == row["inserted"] == 0 for row in repeated.values())
    with pg_container.uow() as repo:
        assert "hashed_password" not in repo.get("master_users", "user-1")
        assert repo.count("resource_versions", {}) == 0
    with TestClient(create_app(pg_container)) as client:
        assert (
            client.get("/api/v1/resources", params={"page": 1, "size": 20, "q": ""}).status_code
            == 200
        )
        assert client.get("/api/v1/resources/resource-1").status_code == 200


@pytest.mark.parametrize(
    "sql,stream,table,target,column,expected",
    [
        (
            "UPDATE providers SET name='New publisher' WHERE id='provider-2'",
            "resources",
            "master_learning_resources",
            "resource-1",
            "provider_name",
            "New publisher",
        ),
        (
            "UPDATE resource_types SET name_vi='New type' WHERE id='type-2'",
            "resources",
            "master_learning_resources",
            "resource-1",
            "resource_type_name_vi",
            "New type",
        ),
        (
            "UPDATE roles SET name='New role' WHERE id='role-1'",
            "users",
            "master_users",
            "user-1",
            "role_name",
            "New role",
        ),
        (
            "UPDATE education_levels SET name_vi='New education' WHERE id='education-1'",
            "grades",
            "master_grade_levels",
            "grade-1",
            "education_name_vi",
            "New education",
        ),
        (
            "UPDATE grade_levels SET name_vi='New grade' WHERE id='grade-1'",
            "grades",
            "master_grade_levels",
            "grade-1",
            "grade_name_vi",
            "New grade",
        ),
        (
            "UPDATE subjects SET subject_name='New subject' WHERE id='subject-1'",
            "subjects",
            "subjects",
            "subject-1",
            "subject_name",
            "New subject",
        ),
        (
            "UPDATE learning_resources SET is_active=false,deleted_at=clock_timestamp() WHERE id='resource-1'",
            "resources",
            "master_learning_resources",
            "resource-1",
            "is_active",
            False,
        ),
    ],
)
def test_dependency_incremental(source, pg_container, sql, stream, table, target, column, expected):
    use_source(pg_container, source)
    pg_container.sync.run(full=True)
    execute(source, sql)
    result = pg_container.sync.run(stream)[stream]
    assert result['updated'] >= 1
    assert result['records_read'] == result['inserted'] + result['updated'] + result['skipped']
    with pg_container.uow() as repo:
        assert repo.get('sync_runs', result['run_id'])['sync_mode'] == 'INCREMENTAL'
        assert repo.get(table, target)[column] == expected


def test_relation_add_move_and_delete_last(source, pg_container):
    use_source(pg_container, source)
    pg_container.sync.run(full=True)
    execute(source, "INSERT INTO resource_subjects VALUES ('resource-1','subject-2',false)")
    pg_container.sync.run("resources")
    with pg_container.uow() as repo:
        assert repo.count("resource_subjects", {"resource_id": "resource-1"}) == 2
    execute(
        source,
        "UPDATE resource_subjects SET resource_id='resource-3' WHERE resource_id='resource-1' AND subject_id='subject-2'",
    )
    pg_container.sync.run("resources")
    with pg_container.uow() as repo:
        assert repo.count("resource_subjects", {"resource_id": "resource-3"}) == 2
    execute(source, "DELETE FROM resource_subjects WHERE resource_id='resource-1'")
    execute(source, "DELETE FROM resource_grade_levels WHERE resource_id='resource-1'")
    pg_container.sync.run("resources")
    with pg_container.uow() as repo:
        assert repo.count("resource_subjects", {"resource_id": "resource-1"}) == 0
        assert repo.count("resource_grade_levels", {"resource_id": "resource-1"}) == 0


def test_null_source_preserved_and_visible(source, pg_container):
    execute(
        source, "UPDATE learning_resources SET provider_id=NULL,title=NULL WHERE id='resource-1'"
    )
    execute(source, "UPDATE users SET full_name=NULL WHERE id='user-1'")
    use_source(pg_container, source)
    pg_container.sync.run(full=True)
    with pg_container.uow() as repo:
        resource = repo.get("master_learning_resources", "resource-1")
        assert (
            resource["provider_id"]
            is resource["provider_name"]
            is resource["provider_is_active"]
            is resource["title"]
            is None
        )
        assert repo.get("master_users", "user-1")["full_name"] is None
    with TestClient(create_app(pg_container)) as client:
        result = client.get("/api/v1/resources/resource-1")
        assert result.status_code == 200 and result.json()["title"] is None
        assert (
            client.patch(
                "/api/v1/resources/resource-1/metadata", json={"publication_status": "DRAFT"}
            ).status_code
            == 409
        )


def test_read_only_and_soft_delete_contract(source):
    with pytest.raises(DomainError, match="read-only"):
        list(PostgreSQLSourceAdapter(engine=source.engine).batches("resources"))
    with source.reader_engine.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("UPDATE providers SET name='not allowed'"))
    with pytest.raises(DBAPIError):
        execute(source, "DELETE FROM learning_resources WHERE id='resource-1'")
    with source.reader_engine.connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("SELECT * FROM resource_versions"))


def test_catalog_sql_and_data_safe_trigger_revision(source, pg_container):
    scripts = Path(__file__).parents[3] / "scripts/sql"
    for name, engine in [
        ("verify_source.sql", source.engine),
        ("verify_master.sql", pg_container.engine),
    ]:
        with engine.connect() as connection:
            for query in (scripts / name).read_text(encoding="utf-8").split(";"):
                if query.strip():
                    rows = connection.execute(text(query)).fetchall()
                    if "FROM id_counters" not in query:
                        assert rows
    with source.engine.connect() as connection:
        before = connection.execute(text("SELECT * FROM learning_resources ORDER BY id")).all()
    migrate_source(source.engine, "s0001", downgrade=True)
    migrate_source(source.engine)
    migrate_source(source.engine)
    with source.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT * FROM learning_resources ORDER BY id")).all() == before
        )


def test_snapshot_consistency_and_late_commit_replay(source, pg_container):
    use_source(pg_container, source)
    pg_container.sync.run(full=True)
    snapshot = source.adapter.batches("resources", batch_size=1)
    first = next(snapshot)
    execute(source, "UPDATE providers SET name='committed during snapshot' WHERE id='provider-2'")
    rest = [a for batch in snapshot for a in batch]
    assert all(a.values["provider_name"] != "committed during snapshot" for a in first + rest)
    with source.engine.connect() as older, older.begin():
        older.execute(
            text("UPDATE learning_resources SET title='late commit' WHERE id='resource-2'")
        )
        execute(source, "UPDATE learning_resources SET title='newer commit' WHERE id='resource-1'")
        pg_container.sync.run("resources")
    pg_container.sync.run("resources")
    with pg_container.uow() as repo:
        assert repo.get("master_learning_resources", "resource-2")["title"] == "late commit"


def test_incremental_candidates_replay_checkpoint_not_wall_clock(source, pg_container, monkeypatch):
    use_source(pg_container, source)
    initial = pg_container.sync.run(full=True)['resources']
    repeated = pg_container.sync.run('resources', full=False)['resources']
    assert repeated['records_read'] == repeated['skipped'] == initial['records_read']
    assert repeated['inserted'] == repeated['updated'] == 0
    with pg_container.uow() as repo:
        before = repo.get('sync_checkpoints', 'resources')
        assert repo.get('sync_runs', repeated['run_id'])['sync_mode'] == 'INCREMENTAL'
    later = datetime.now(UTC) + timedelta(days=30)

    class LaterClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return later

    monkeypatch.setattr('data_sync_etl.adapters.source.postgresql.datetime', LaterClock)
    unchanged = pg_container.sync.run('resources', full=False)['resources']
    assert unchanged['records_read'] == unchanged['skipped'] == initial['records_read']
    # Relation inserts in the seed touch all resource timestamps. Use a known
    # later marker in this isolated fixture, so the other candidates are >300s
    # behind the new checkpoint without sleeping or changing runtime replay.
    execute(source, "UPDATE learning_resources SET title='one changed candidate', updated_at=:stamp WHERE id='resource-1'",
            stamp=before['last_updated_at'] + timedelta(seconds=600))
    changed = pg_container.sync.run('resources', full=False)['resources']
    assert changed['updated'] == 1 and changed['inserted'] == 0
    # New checkpoint moves past old fixture timestamps. Only the last changed row
    # remains in the replay window; restart must preserve that cursor and counters.
    pg_container.sync.source = PostgreSQLSourceAdapter(engine=source.reader_engine, replay_seconds=300)
    last = pg_container.sync.run('resources', full=False)['resources']
    assert last['records_read'] == last['skipped'] == 1 and last['updated'] == last['inserted'] == 0
    with pg_container.uow() as repo:
        after = repo.get('sync_checkpoints', 'resources')
        assert (after['last_updated_at'], after['last_id']) >= (before['last_updated_at'], before['last_id'])
        for data in (repeated, unchanged, changed, last):
            assert data['records_read'] == data['inserted'] + data['updated'] + data['skipped']


def test_repeated_concurrent_updates_have_new_markers(source):
    def update(number):
        execute(
            source,
            "UPDATE learning_resources SET title=:title WHERE id='resource-1'",
            title=f"value-{number}",
        )

    with source.engine.connect() as connection:
        before = connection.scalar(
            text("SELECT updated_at FROM learning_resources WHERE id='resource-1'")
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(update, range(4)))
    with source.engine.connect() as connection:
        after = connection.scalar(
            text("SELECT updated_at FROM learning_resources WHERE id='resource-1'")
        )
    assert after > before + timedelta(microseconds=3)
    execute(source, "UPDATE learning_resources SET title=title WHERE id='resource-1'")
    with source.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT updated_at FROM learning_resources WHERE id='resource-1'")
            )
            == after
        )
