"""SOURCE SELECT-only catalog SQL and Master merged pagination on isolated PG."""
import pytest
from sqlalchemy import event, text

from data_sync_etl.ports.source_catalogs import SourceCatalogError
from tests.integration.source_postgres.conftest import source_database as source_database
from tests.unit.test_source_catalogs import (  # noqa: F401
    catalog,
    test_catalog_api_additive_legacy_rows_and_sanitized_error,
    test_empty_fallback_is_not_verified_empty_source,
    test_full_source_options_create_and_update_local_without_rewriting_history,
    test_full_source_unused_catalog_count_page_unicode,
    test_inactive_source_choice_and_source_owned_patch_stay_blocked,
    test_offline_empty_and_recovery_are_distinct,
    test_source_precedence_inactive_deleted_and_same_name,
    test_types_unused_and_local_and_real_code_collision,
)

pytestmark = pytest.mark.postgres


@pytest.fixture
def container(pg_container):
    return pg_container


@pytest.fixture
def source(source_database):
    from pathlib import Path

    from scripts.source_seed import seed_demo
    with source_database.engine.begin() as connection:
        seed_demo(connection, Path(__file__).parents[2] / "fixtures/source")
    return source_database


def test_real_source_catalog_unused_and_select_only(source, pg_container):
    with source.engine.begin() as connection:
        connection.execute(text("INSERT INTO providers(id,name,provider_type,is_active,created_at,updated_at) "
            "SELECT 'unused-'||n,'Đơn vị '||lpad(n::text,4,'0'),'PUBLISHER',true,now(),now() FROM generate_series(1,1100) n"))
        connection.execute(text("INSERT INTO resource_types(id,code,name_vi,is_active,created_at,updated_at) "
            "VALUES ('unused-type','UNUSED','Chưa dùng',true,now(),now()),('disabled-type','DISABLED','Disabled',false,now(),now())"))
    statements = []
    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
    event.listen(source.reader_engine, "before_cursor_execute", capture)
    try:
        service = pg_container.resource_lookups
        service.source = source.adapter
        first = service.list_providers(page=1, size=7, query="ĐƠN VỊ")
        assert first["pagination"]["total"] == 1100
        assert first["items"][0]["name"] == "Đơn vị 0001"
        assert len(service.list_providers(page=158, size=7, query="ĐƠN VỊ")["items"]) == 1
        types = service.list_resource_types(page=1, size=100, query="CHƯA")
        assert types["items"] == [{"code": "UNUSED", "name": "Chưa dùng"}]
        assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
        assert len([s for s in statements if "FROM providers ORDER BY" in s]) == 2
        assert not any("FROM learning_resources" in s for s in statements)
    finally:
        event.remove(source.reader_engine, "before_cursor_execute", capture)


def test_source_permission_error_is_not_fallback(source):
    with source.engine.begin() as connection:
        connection.execute(text(f'REVOKE SELECT ON "{source.schema}".providers FROM "{source.role}"'))
    with pytest.raises(SourceCatalogError) as error:
        source.adapter.catalog_rows("providers")
    assert error.value.status == "SOURCE_ERROR"


def test_catalog_over_twenty_thousand_rows_is_503_not_partial_or_empty(source, pg_container):
    from fastapi.testclient import TestClient

    from data_sync_etl.main import create_app

    with source.engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO providers(id,name,provider_type,is_active,created_at,updated_at) "
            "SELECT 'budget-'||n,'Catalog budget '||n,'PUBLISHER',true,now(),now() "
            "FROM generate_series(1,20001) n"
        ))
    with pytest.raises(SourceCatalogError) as error:
        source.adapter.catalog_rows("providers")
    assert error.value.status == "SOURCE_LIMIT_EXCEEDED"
    pg_container.resource_lookups.source = source.adapter
    with TestClient(create_app(pg_container)) as api:
        response = api.get("/api/v1/providers")
    assert response.status_code == 503
    assert "items" not in response.json()
    assert "postgresql://" not in response.text and "postgresql+psycopg://" not in response.text


def test_catalog_timeout_is_transaction_local_not_shared_with_other_reads(source):
    with source.reader_engine.connect() as connection:
        before = connection.scalar(text("SHOW statement_timeout"))
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(source.reader_engine, "before_cursor_execute", capture)
    try:
        source.adapter.catalog_rows("providers")
        assert any("set_config('statement_timeout','3000',true)" in sql for sql in statements)
        statements.clear()
        source.adapter.get_provider("provider-1")
        assert not any("statement_timeout" in sql for sql in statements)
        with source.reader_engine.connect() as connection:
            assert connection.scalar(text("SHOW statement_timeout")) == before
    finally:
        event.remove(source.reader_engine, "before_cursor_execute", capture)



def test_unused_grades_and_subjects_already_sync_as_full_catalogs(source, pg_container):
    from data_sync_etl.application.sync import MasterSync
    with source.engine.begin() as connection:
        connection.execute(text("INSERT INTO grade_levels(id,education_level_id,code,name_vi,ordinal,is_active,created_at,updated_at) "
            "SELECT 'unused-grade',id,'G88','Khối chưa dùng',88,true,now(),now() FROM education_levels ORDER BY id LIMIT 1"))
        connection.execute(text("INSERT INTO subjects(id,grade_id,subject_name,subject_slug,is_active,created_at,updated_at) "
            "VALUES ('unused-subject','unused-grade','Môn chưa dùng','unused-subject',true,now(),now())"))
    c = pg_container
    MasterSync(source.adapter, c.uow, batch_size=100).run(full=True)
    grades = c.resource_lookups.list_grade_levels(page=1, size=100, query="G88")
    assert grades["items"] == [{"id": "unused-grade", "grade_code": "G88", "grade_name_vi": "Khối chưa dùng"}]
    assert grades["catalog"]["completeness"] == "MASTER_MANAGED"
    subjects = c.subject_listing.execute(page=1, size=100, grade_level_id="unused-grade")
    assert subjects["items"] == [{"id": "unused-subject", "name": "Môn chưa dùng", "slug": "unused-subject", "grade_level_id": "unused-grade"}]


def test_schema_counts_are_preserved_in_isolated_databases(source, pg_container, mongo_test_database):
    from pymongo import MongoClient
    from sqlalchemy import inspect

    from data_sync_etl.adapters.mongo.schema import initialize
    assert len(set(inspect(source.engine).get_table_names(schema=source.schema)) - {"alembic_source_version"}) == 15
    with pg_container.engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    assert len(set(inspect(pg_container.engine).get_table_names(schema=schema)) - {"alembic_version"}) == 18
    with MongoClient(mongo_test_database[0], serverSelectionTimeoutMS=3000) as client:
        db = client[mongo_test_database[1]]
        assert initialize(db, apply=True, dimension=384)["ready"]
        assert len(db.list_collection_names()) == 5
