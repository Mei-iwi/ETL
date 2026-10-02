import ast
from pathlib import Path

from conftest import migrate
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from data_sync_etl.cli.main import demo_run, parser
from data_sync_etl.db.models import Base
from data_sync_etl.main import create_app


def test_health_admin_disabled(container):
    with TestClient(create_app(container)) as client:
        response = client.get("/health")
        assert response.status_code == 200 and response.json()["database"] == "ok"
        assert response.headers["X-Correlation-ID"]
        assert client.post("/admin/sync/master", json={"full": True}).status_code == 403


def test_api_and_validation_redaction(container):
    container.settings.admin_enabled = True
    with TestClient(create_app(container)) as client:
        response = client.post("/admin/sync/master", json={"full": True})
        assert response.status_code == 200
        assert "hashed_password" not in response.text
        response = client.post("/admin/sync/master", json={"stream": "secret-token"})
        assert response.status_code == 422 and "secret-token" not in response.text
        assert client.get("/admin/resource-versions/missing/etl-status").status_code == 400


def test_migration_up_down_up(container):
    assert set(Base.metadata.tables) <= set(inspect(container.engine).get_table_names())
    migrate(container.engine, "base", downgrade=True)
    assert inspect(container.engine).get_table_names() == ["alembic_version"]
    migrate(container.engine)
    assert len(inspect(container.engine).get_table_names()) == 16
    assert len(inspect(container.engine).get_foreign_keys("ocr_page_tasks")) == 1


def test_cli_demo_and_rerun(container):
    assert parser().parse_args(["sync-master", "--full"]).full
    assert demo_run(container, "resource-1")["content_unit_count"] == 3
    assert demo_run(container, "resource-1")["action"] == "NO_CHANGE"


def test_architecture_dependencies():
    root = Path(__file__).parents[2] / "src/data_sync_etl"
    for folder in ("domain", "application", "ports"):
        for path in (root / folder).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    assert not module.startswith(
                        ("sqlalchemy", "fastapi", "data_sync_etl.adapters", "data_sync_etl.db")
                    ), path
