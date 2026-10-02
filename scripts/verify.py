"""Run QA; --postgres prefers TEST_DATABASE_URL and requires real PostgreSQL tests."""

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url

from data_sync_etl.config import Settings


def validate_test_url(value, environment):
    """Fail closed on malformed/known-production targets without echoing the URL."""
    try:
        url = make_url(value)
        if url.drivername not in ("postgresql", "postgresql+psycopg") or not url.database:
            raise ValueError
        if not url.host or url.port == 0:
            raise ValueError
        forbidden = {"options", "host", "hostaddr", "port", "dbname", "service", "servicefile"}
        if forbidden.intersection(url.query):
            raise ValueError
    except Exception:
        raise ValueError("Invalid PostgreSQL test configuration") from None
    labels = set(re.split(r"[^a-z0-9]+", f"{url.host} {url.database}".lower()))
    modes = {environment.get(k, "").lower() for k in ("APP_ENV", "ENVIRONMENT", "ENV")}
    if {"prod", "production"}.intersection(labels | modes):
        raise ValueError("Refusing tests against an explicitly production-labelled target")
    local = {"localhost", "127.0.0.1", "::1", "postgres"}
    if url.host not in local and environment.get("ETL_TEST_DB_ALLOW_REMOTE") != "true":
        raise ValueError(
            "Remote test DB requires ETL_TEST_DB_ALLOW_REMOTE=true and an isolated test database"
        )
    return url.set(drivername="postgresql+psycopg")


def select_test_url(environment, settings, host=None):
    if "TEST_DATABASE_URL" in environment:
        selected = environment["TEST_DATABASE_URL"]
    else:
        selected = settings.test_database_url or settings.database_url
    # Validate the original selection too; a host override must not bypass production checks.
    url = validate_test_url(selected, environment)
    if host:
        url = validate_test_url(url.set(host=host), environment)
    return url.render_as_string(hide_password=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--postgres", action="store_true")
    parser.add_argument("--db-host")
    args = parser.parse_args()
    env = os.environ.copy()
    if args.postgres:
        try:
            env["TEST_DATABASE_URL"] = select_test_url(env, Settings(), args.db_host)
        except Exception:
            print(
                "QA test DB configuration rejected; see documented safety guards.", file=sys.stderr
            )
            return 2
        env["ETL_REQUIRE_POSTGRES"] = "1"
    else:
        # Default QA is independent of a user's ambient DB configuration.
        env.pop("TEST_DATABASE_URL", None)
        env.pop("ETL_REQUIRE_POSTGRES", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    scratch = Path(".test-tmp").resolve()
    scratch.mkdir(exist_ok=True)
    temporary = tempfile.mkdtemp(prefix="qa-", dir=scratch)
    commands = [
        [sys.executable, "-m", "ruff", "check", "src", "tests", "alembic", "scripts"],
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--check",
            "src",
            "tests",
            "alembic",
            "scripts",
        ],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(Path(temporary) / "pytest"),
        ],
    ]
    for command in commands:
        result = subprocess.run(command, env=env, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
