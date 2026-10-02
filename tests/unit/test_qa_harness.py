from types import SimpleNamespace

import pytest
from sqlalchemy.engine import make_url

from scripts.verify import select_test_url, validate_test_url


def settings(test=None):
    return SimpleNamespace(
        database_url="postgresql+psycopg://dev:dev-secret@localhost/etl", test_database_url=test
    )


def test_explicit_test_url_wins_and_host_only_override():
    env = {
        "TEST_DATABASE_URL": "postgresql+psycopg://tester:test-secret@localhost:5433/etl_test?sslmode=require"
    }
    selected = make_url(select_test_url(env, settings(), "postgres"))
    assert (selected.username, selected.password, selected.database, selected.port) == (
        "tester",
        "test-secret",
        "etl_test",
        5433,
    )
    assert selected.host == "postgres" and selected.query["sslmode"] == "require"


def test_dotenv_test_then_dev_fallback():
    assert make_url(select_test_url({}, settings())).database == "etl"
    assert (
        make_url(select_test_url({}, settings("postgresql://localhost/etl_test"))).database
        == "etl_test"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "secret-invalid-url",
        "sqlite:///test.db",
        "postgresql://localhost/production",
        "postgresql://prod-db/etl_test",
        "postgresql://localhost/etl?host=prod-db",
        "postgresql://localhost/etl?options=-csearch_path=public",
    ],
)
def test_unsafe_explicit_url_does_not_fallback(value):
    with pytest.raises(ValueError) as caught:
        select_test_url({"TEST_DATABASE_URL": value}, settings())
    assert "secret-invalid-url" not in str(caught.value)


def test_production_environment_and_remote_guard():
    with pytest.raises(ValueError):
        validate_test_url("postgresql://localhost/etl", {"APP_ENV": "production"})
    with pytest.raises(ValueError):
        validate_test_url("postgresql://remote-test/etl_test", {})
    assert (
        validate_test_url(
            "postgresql://remote-test/etl_test", {"ETL_TEST_DB_ALLOW_REMOTE": "true"}
        ).host
        == "remote-test"
    )
