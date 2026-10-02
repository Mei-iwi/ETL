import os
import shutil
import sys
from pathlib import Path
from uuid import uuid4

import pymupdf
import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from data_sync_etl.composition import Container
from data_sync_etl.config import Settings
from data_sync_etl.db.session import make_engine

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


def pytest_sessionfinish(session, exitstatus):
    if os.environ.get("ETL_REQUIRE_POSTGRES") == "1":
        reporter = session.config.pluginmanager.get_plugin("terminalreporter")
        selected = [item for item in session.items if item.get_closest_marker("postgres")]
        skipped = [r for r in reporter.stats.get("skipped", []) if "integration/" in r.nodeid]
        if not selected or skipped:
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            reporter.write_line("Required PostgreSQL tests were not executed; refusing PASS")


def migrate(engine, revision="head", downgrade=False):
    config = Config(str(ROOT / "alembic.ini"))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        (command.downgrade if downgrade else command.upgrade)(config, revision)


@pytest.fixture
def container(tmp_path):
    source = tmp_path / "fixtures"
    shutil.copytree(ROOT / "fixtures/source", source)
    engine = make_engine("sqlite:///" + (tmp_path / "test.db").as_posix())
    migrate(engine)
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        storage_root=tmp_path / "storage",
        source_fixture_root=source,
        sync_batch_size=2,
        ocr_engine="fake",
        source_adapter="json",
        admin_enabled=False,
    )
    result = Container(settings, engine)
    yield result
    engine.dispose()


@pytest.fixture
def pg_container(tmp_path):
    from scripts.verify import validate_test_url

    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("ETL_REQUIRE_POSTGRES") == "1":
            pytest.fail("Required PostgreSQL test URL missing", pytrace=False)
        pytest.skip(
            "TEST_DATABASE_URL not configured; PostgreSQL concurrency requires a real server"
        )
    try:
        selected = validate_test_url(url, os.environ)
    except ValueError:
        pytest.fail("Unsafe PostgreSQL test configuration", pytrace=False)
    engine = make_engine(selected)
    schema = "etl_test_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    from sqlalchemy import event

    @event.listens_for(engine, "checkout")
    def set_search_path(connection, record, proxy):
        # SET SESSION must survive the first transaction rollback.
        old = connection.autocommit
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(f'SET search_path TO "{schema}"')
        connection.autocommit = old

    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
        migrate(engine)
        source = tmp_path / "fixtures"
        shutil.copytree(ROOT / "fixtures/source", source)
        yield Container(
            Settings(
                _env_file=None,
                storage_root=tmp_path / "storage",
                source_fixture_root=source,
                ocr_engine="fake",
                source_adapter="json",
                admin_enabled=False,
                sync_batch_size=2,
            ),
            engine,
        )
    finally:
        with engine.begin() as connection:
            assert schema.startswith("etl_test_") and len(schema) == 41
            assert connection.scalar(text("SELECT current_schema()")) == schema
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


@pytest.fixture
def pdf_file(container):
    return make_pdf(container)


def make_pdf(container, texts=None, name="sample.pdf"):
    path = container.storage.get_local_path("input", name)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as pdf:
        for value in texts or ["Page one text", "Page two text", "Page three text"]:
            page = pdf.new_page()
            if value:
                page.insert_text((72, 72), value)
        pdf.save(path)
    return path


def ingest(container, texts=None):
    container.sync.run(full=True)
    make_pdf(container, texts)
    return container.ingestion.run("resource-1", "input", "sample.pdf")["version"]


def completed(container, texts=None):
    version = ingest(container, texts)
    job = container.ocr.create_job(version["id"])
    while container.worker.once("test-worker"):
        pass
    return version, job
