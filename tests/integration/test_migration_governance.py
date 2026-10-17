"""Schema tests confined to the existing TEST_DATABASE_URL UUID-schema fixture."""

import re

import pytest
from conftest import migrate
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, inspect, text

from data_sync_etl.db.models import Base
from scripts.audit_erd_contract import trigger_inventory
from scripts.check_migration_governance import canonical_functions, read

pytestmark = pytest.mark.postgres


def normalized_check(value):
    value = re.sub(r"::(?:text|character varying)", "", value.lower())
    return re.sub(r'[\s()"]', "", value)


def test_all_eighteen_master_tables_match_orm_after_migration(pg_container):
    with pg_container.engine.connect() as connection:
        inspector = inspect(connection)
        assert set(inspector.get_table_names()) == set(Base.metadata.tables) | {"alembic_version"}
        for name, table in Base.metadata.tables.items():
            actual = {column["name"]: column for column in inspector.get_columns(name)}
            assert set(actual) == set(table.columns.keys()), name
            for column in table.columns:
                reflected = actual[column.name]
                assert reflected["nullable"] == column.nullable, (name, column.name)
                assert str(reflected["type"].compile(dialect=connection.dialect)) == str(
                    column.type.compile(dialect=connection.dialect)
                ), (name, column.name)
            assert inspector.get_pk_constraint(name)["constrained_columns"] == [
                c.name for c in table.primary_key.columns
            ]
            expected_fks = {
                (
                    tuple(c.name for c in constraint.columns),
                    constraint.elements[0].column.table.name,
                    tuple(element.column.name for element in constraint.elements),
                    constraint.ondelete,
                )
                for constraint in table.constraints
                if isinstance(constraint, ForeignKeyConstraint)
            }
            actual_fks = {
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk.get("options", {}).get("ondelete"),
                )
                for fk in inspector.get_foreign_keys(name)
            }
            assert actual_fks == expected_fks, name
            unique = {
                tuple(item["column_names"]) for item in inspector.get_unique_constraints(name)
            }
            assert unique == {
                tuple(c.name for c in constraint.columns)
                for constraint in table.constraints
                if isinstance(constraint, UniqueConstraint)
            }, name
            indexes = {
                index["name"]: index
                for index in inspector.get_indexes(name)
                if not index.get("duplicates_constraint")
            }
            assert set(indexes) == {index.name for index in table.indexes}, name
            for index in table.indexes:
                actual_index = indexes[index.name]
                assert actual_index["column_names"] == [c.name for c in index.columns]
                assert bool(actual_index["unique"]) == bool(index.unique)
                assert bool(
                    actual_index.get("dialect_options", {}).get("postgresql_where")
                ) == bool(index.dialect_options["postgresql"]["where"] is not None)
            checks = {
                normalized_check(item["sqltext"]) for item in inspector.get_check_constraints(name)
            }
            expected_checks = {
                normalized_check(str(item.sqltext))
                for item in table.constraints
                if hasattr(item, "sqltext")
            }
            assert checks == expected_checks, (name, checks, expected_checks)


def test_master_trigger_names_and_function_bodies_match_canonical_reference(pg_container):
    with pg_container.engine.connect() as connection:
        names = set(
            connection.scalars(
                text(
                    "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND NOT t.tgisinternal"
                )
            )
        )
        assert names == {row["trigger"] for row in trigger_inventory() if row["layer"] == "MASTER"}
        functions = dict(
            connection.execute(
                text(
                    "SELECT p.proname,p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=current_schema() AND p.prorettype='trigger'::regtype"
                )
            ).all()
        )
        for name, spec in canonical_functions().items():
            if spec["layer"] == "MASTER":
                body = spec["sql"].split("$$")[1]
                assert " ".join(functions[name].split()) == " ".join(body.split())


def test_catalog_and_master_preflight_run_in_read_only_transaction(pg_container):
    with pg_container.engine.begin() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        for path in ("scripts/sql/verify/catalog.sql", "scripts/sql/verify/master_preflight.sql"):
            sql = re.sub(r"--[^\n]*", "", read(path))
            for statement in sql.split(";"):
                if statement.strip():
                    connection.execute(text(statement)).all()


def test_upgrade_from_0013_and_idempotent_head_preserves_catalog(pg_container):
    migrate(pg_container.engine, "0013", downgrade=True)
    inspector = inspect(pg_container.engine)
    assert "grade_mongo_id" not in {c["name"] for c in inspector.get_columns("master_grade_levels")}
    migrate(pg_container.engine)
    migrate(pg_container.engine)
    with pg_container.engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0015"
        assert len(inspect(connection).get_table_names()) == 19
        assert connection.scalar(text("SELECT count(*) FROM master_learning_resources")) == 0


@pytest.mark.xfail(
    strict=True,
    reason="B13: frozen 0005 downgrade uses the wrong grade index name; no approval to rewrite history",
)
def test_historical_0005_downgrade_removes_the_created_index(pg_container):
    migrate(pg_container.engine, "0004", downgrade=True)
    indexes = {item["name"] for item in inspect(pg_container.engine).get_indexes("subjects")}
    assert "ix_subject_grade_active_deleted" not in indexes
