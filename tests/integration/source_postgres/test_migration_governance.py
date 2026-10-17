"""SOURCE canonical references checked only in the isolated SOURCE fixture."""

import re

import pytest
from sqlalchemy import text

from scripts.audit_erd_contract import trigger_inventory
from scripts.check_migration_governance import canonical_functions, read

pytestmark = pytest.mark.postgres


def test_source_trigger_names_and_bodies_match_current_reference(source_database):
    with source_database.engine.connect() as connection:
        names = set(
            connection.scalars(
                text(
                    "SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname=current_schema() AND NOT t.tgisinternal"
                )
            )
        )
        assert names == {row["trigger"] for row in trigger_inventory() if row["layer"] == "SOURCE"}
        functions = dict(
            connection.execute(
                text(
                    "SELECT p.proname,p.prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                    "WHERE n.nspname=current_schema() AND p.prorettype='trigger'::regtype"
                )
            ).all()
        )
        for name, spec in canonical_functions().items():
            if spec["layer"] == "SOURCE":
                assert " ".join(functions[name].split()) == " ".join(
                    spec["sql"].split("$$")[1].split()
                )


def test_source_catalog_verification_is_read_only(source_database):
    with source_database.engine.begin() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        sql = re.sub(r"--[^\n]*", "", read("scripts/sql/verify/catalog.sql"))
        for statement in sql.split(";"):
            if statement.strip():
                connection.execute(text(statement)).all()
