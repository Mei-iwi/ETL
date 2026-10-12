from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config
from conftest import make_pdf, migrate
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from alembic import command
from data_sync_etl.adapters.source.id_counter import PostgreSQLSourceIDCounter
from data_sync_etl.application.source_ids import SourceIDService
from data_sync_etl.domain.core import DomainError

from .conftest import ROOT, migrate_source
from .test_source import execute, use_source

pytestmark = pytest.mark.postgres
JUNE = datetime(2026, 6, 1, tzinfo=UTC)


def allocate(source, created_at=JUNE):
    with source.engine.begin() as connection:
        return SourceIDService(PostgreSQLSourceIDCounter(connection)).next_id(
            "RES", created_at=created_at
        )


def test_counter_concurrency_month_rollback_overflow(source):
    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(lambda _: allocate(source), range(30)))
    assert len(set(ids)) == 30
    assert sorted(ids) == [f"RES0626{n:05d}" for n in range(1, 31)]
    assert allocate(source, datetime(2026, 7, 1, tzinfo=UTC)) == "RES072600001"
    with pytest.raises(RuntimeError), source.engine.begin() as connection:
        identifier = SourceIDService(PostgreSQLSourceIDCounter(connection)).next_id(
            "RES", created_at=JUNE
        )
        # Counter and entity write share the caller's transaction.
        connection.execute(
            text(
                "INSERT INTO learning_resources(id,resource_type_id,created_by,title,publication_status,is_active,created_at,updated_at) VALUES(:id,'type-1','user-1','rollback','DRAFT',true,:stamp,:stamp)"
            ),
            dict(id=identifier, stamp=JUNE),
        )
        raise RuntimeError("controlled rollback")
    assert allocate(source) == identifier
    with source.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM learning_resources WHERE id=:id"), dict(id=identifier)
            )
            == 0
        )
    execute(source, "UPDATE id_counters SET last_seq=99999 WHERE prefix='RES' AND period='0626'")
    with pytest.raises(DomainError, match="exhausted"):
        allocate(source)
    with source.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT last_seq FROM id_counters WHERE prefix='RES' AND period='0626'")
            )
            == 99999
        )
    with source.reader_engine.begin() as connection, pytest.raises(DBAPIError):
        SourceIDService(PostgreSQLSourceIDCounter(connection)).next_id("RES", created_at=JUNE)


def test_counter_requires_transaction_and_source(source, pg_container):
    with source.engine.connect() as connection, pytest.raises(DomainError, match="transaction"):
        PostgreSQLSourceIDCounter(connection)
    with (
        source.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection,
        connection.begin(),
        pytest.raises(DomainError, match="transaction"),
    ):
        PostgreSQLSourceIDCounter(connection)
    with (
        pg_container.engine.begin() as connection,
        pytest.raises(DomainError, match="SOURCE schema"),
    ):
        PostgreSQLSourceIDCounter(connection)


def test_source_timestamp_immutability_noop_future_and_replay(source, pg_container):
    use_source(pg_container, source)
    pg_container.sync.run(full=True)
    with source.engine.connect() as connection:
        before = connection.execute(
            text("SELECT created_at,updated_at FROM learning_resources WHERE id='resource-1'")
        ).one()
    with pytest.raises(DBAPIError):
        execute(
            source,
            "UPDATE learning_resources SET created_at=created_at+interval '1 day' WHERE id='resource-1'",
        )
    execute(
        source,
        "UPDATE learning_resources SET updated_at=updated_at-interval '1 day' WHERE id='resource-1'",
    )
    execute(source, "UPDATE learning_resources SET title=title WHERE id='resource-1'")
    with source.engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT updated_at FROM learning_resources WHERE id='resource-1'")
            )
            == before.updated_at
        )
    future = datetime.now(UTC) + timedelta(days=2)
    execute(
        source,
        "UPDATE learning_resources SET title='future marker',updated_at=:stamp WHERE id='resource-1'",
        stamp=future,
    )
    pg_container.sync.run("resources")
    execute(
        source,
        "UPDATE learning_resources SET title='other after future checkpoint' WHERE id='resource-2'",
    )
    pg_container.sync.run("resources")
    with pg_container.uow() as repo:
        assert (
            repo.get("master_learning_resources", "resource-2")["title"]
            == "other after future checkpoint"
        )
    execute(
        source,
        "UPDATE learning_resources SET title='next marker',updated_at=:stamp WHERE id='resource-1'",
        stamp=JUNE,
    )
    pg_container.sync.run("resources")
    with pg_container.uow() as repo:
        assert repo.get("master_learning_resources", "resource-1")["title"] == "next marker"
    with source.engine.connect() as connection:
        after = connection.execute(
            text("SELECT created_at,updated_at FROM learning_resources WHERE id='resource-1'")
        ).one()
        assert after.created_at == before.created_at and after.updated_at > future


def test_source_numeric_checks_and_preflight(source):
    with source.engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO resource_versions(id,resource_id,version_number,lifecycle_status,storage_bucket,storage_object_key,source_hash,uploaded_by,created_at,updated_at) VALUES ('version-check','resource-1',1,'UPLOADED','test','test.pdf',:hash,'user-1',:stamp,:stamp)"
            ),
            dict(hash="a" * 64, stamp=JUNE),
        )
        connection.execute(
            text(
                "INSERT INTO content_units(id,resource_version_id,unit_type,sequence_no,review_status,retrieval_eligible,created_at,updated_at) VALUES('unit-check','version-check','SECTION',1,'PENDING',false,:stamp,:stamp)"
            ),
            dict(stamp=JUNE),
        )
        connection.execute(
            text(
                "INSERT INTO ocr_jobs(id,resource_version_id,triggered_by,status,total_pages,completed_pages,failed_pages,created_at,updated_at) VALUES('job-check','version-check','user-1','QUEUED',2,0,0,:stamp,:stamp)"
            ),
            dict(stamp=JUNE),
        )
        connection.execute(
            text(
                "INSERT INTO ocr_page_tasks(id,job_id,page_num,local_image_path,status,retry_count,created_at,updated_at) VALUES('task-check','job-check',1,'image.png','PENDING',0,:stamp,:stamp)"
            ),
            dict(stamp=JUNE),
        )
    for sql in [
        "UPDATE resource_versions SET version_number=0",
        "UPDATE content_units SET sequence_no=0",
        "UPDATE content_units SET page_from=3,page_to=2",
        "UPDATE ocr_jobs SET failed_pages=3",
        "UPDATE ocr_jobs SET completed_pages=-1",
        "UPDATE ocr_page_tasks SET page_num=0",
        "UPDATE ocr_page_tasks SET retry_count=-1",
    ]:
        with pytest.raises(DBAPIError):
            execute(source, sql)
    migrate_source(source.engine, "s0002", downgrade=True)
    execute(source, "UPDATE ocr_page_tasks SET retry_count=-1")
    with pytest.raises(RuntimeError, match="preflight"):
        migrate_source(source.engine)
    with source.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_source_version")) == "s0002"
    execute(source, "UPDATE ocr_page_tasks SET retry_count=0")
    migrate_source(source.engine)
    with source.engine.connect() as connection:
        assert (
            sum(
                len(inspect(connection).get_columns(t))
                for t in inspect(connection).get_table_names()
                if t != "alembic_source_version"
            )
            == 145
        )


def test_master_timestamp_revision_and_cross_migration_guards(source, pg_container):
    c = pg_container
    c.sync.run(full=True)
    make_pdf(c)
    version = c.ingestion.run("resource-1", "input", "sample.pdf")["version"]
    with c.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE resource_versions SET updated_at=updated_at-interval '1 day' WHERE id=:id"
            ),
            dict(id=version["id"]),
        )
    with c.uow() as repo:
        assert repo.get("resource_versions", version["id"])["updated_at"] == version["updated_at"]
    with pytest.raises(DBAPIError), c.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE resource_versions SET created_at=created_at+interval '1 day' WHERE id=:id"
            ),
            dict(id=version["id"]),
        )
    migrate(c.engine, "0006", downgrade=True)
    migrate(c.engine)
    with pytest.raises(RuntimeError, match="SOURCE schema"):
        migrate(source.engine)
    with pytest.raises(RuntimeError, match="Master schema"):
        migrate_source(c.engine)


def test_reader_script_repeatable_and_trigger_catalog(source):
    sql = Path(ROOT / "scripts/sql/source_reader.sql").read_text(encoding="utf-8")
    sql = (
        sql.replace("etl_source_reader", source.role)
        .replace(
            "ON DATABASE etl_source ",
            "ON DATABASE " + source.engine.dialect.identifier_preparer.quote(source.database) + " ",
        )
        .replace("public.", source.schema + ".")
        .replace("SCHEMA public ", "SCHEMA " + source.schema + " ")
    )
    with source.engine.begin() as connection:
        connection.exec_driver_sql(sql)
        connection.exec_driver_sql(sql)
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE r.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal AND t.tgenabled='O'"
                )
            )
            == 26
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_proc WHERE pronamespace=current_schema()::regnamespace AND prorettype='trigger'::regtype AND NOT prosecdef"
                )
            )
            == 4
        )


def test_normal_migration_environment_commits_and_master_orm_matches(
    source, pg_container, monkeypatch
):
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    from data_sync_etl.db.models import Base

    config = Config(str(ROOT / "alembic_source.ini"))
    config.attributes["database_url"] = source.engine.url
    # Exercise env.py without injected connection, but keep the isolated test schema.
    with monkeypatch.context() as patch:
        patch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: source.engine)
        command.downgrade(config, "s0002")
        command.upgrade(config, "head")
    with source.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_source_version")) == "s0003"
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["database_url"] = pg_container.engine.url
    with monkeypatch.context() as patch:
        patch.setattr("sqlalchemy.create_engine", lambda *args, **kwargs: pg_container.engine)
        command.downgrade(config, "0006")
        command.upgrade(config, "head")
    with pg_container.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007"
        assert (
            compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}), Base.metadata
            )
            == []
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE r.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal AND t.tgenabled='O'"
                )
            )
            == 6
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class r ON r.oid=t.tgrelid WHERE r.relnamespace=current_schema()::regnamespace AND NOT t.tgisinternal AND (r.relname LIKE 'master_%' OR r.relname='subjects')"
                )
            )
            == 0
        )
