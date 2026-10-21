import ast
from contextlib import contextmanager
from pathlib import Path

from conftest import migrate
from sqlalchemy import inspect

from data_sync_etl.cli.main import demo_run, parser
from data_sync_etl.db.models import Base
from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def test_health_admin_disabled(container):
    with TestClient(create_app(container)) as client:
        response = client.get("/health")
        assert response.status_code == 200 and response.json()["database"] == "ok"
        assert response.headers["X-Correlation-ID"]
        assert client.post("/admin/sync/master", json={"full": True}).status_code == 403
        assert client.post("/api/v1/master-data/sync", json={"full": True}).status_code == 403
        assert client.get("/api/v1/master-data/sync/status").status_code == 403
        assert client.patch(
            "/api/v1/master-data/sync/auto", json={"enabled": False}
        ).status_code == 403


def test_master_sync_v1_contract_and_tag(container):
    container.settings.admin_enabled = True
    app = create_app(container)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/master-data/sync",
            json={"stream": "all", "full": True},
        )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"grades", "subjects", "users", "resources"}
    result = body["resources"]
    assert set(result) == {
        "run_id", "records_read", "inserted", "updated", "skipped", "failed"
    }
    operation = app.openapi()["paths"]["/api/v1/master-data/sync"]["post"]
    assert operation["tags"] == ["Learning Resource Ingestion and Management"]
    status_response = client.get("/api/v1/master-data/sync/status")
    assert status_response.status_code == 200
    status = status_response.json()
    assert status["auto_supported"] is False
    assert status["auto_enabled"] is False
    assert status["worker_healthy"] is False
    assert status["auto_running"] is False
    assert status["auto_all_enabled"] is False
    assert len(status["auto_streams"]) == 4
    assert {item["sync_mode"] for item in status["auto_streams"]} == {"INCREMENTAL"}
    assert status["runs"][0]["sync_mode"] == "FULL"
    assert status["runs"][0]["status"] == "COMPLETED"
    status_operation = app.openapi()["paths"]["/api/v1/master-data/sync/status"]["get"]
    assert status_operation["tags"] == ["Learning Resource Ingestion and Management"]
    auto_operation = app.openapi()["paths"]["/api/v1/master-data/sync/auto"]["patch"]
    assert auto_operation["tags"] == ["Learning Resource Ingestion and Management"]
    assert client.patch(
        "/api/v1/master-data/sync/auto", json={"enabled": True}
    ).status_code == 409
    disabled = client.patch(
        "/api/v1/master-data/sync/auto", json={"enabled": False}
    )
    assert disabled.status_code == 200 and disabled.json()["auto_enabled"] is False
    assert client.patch(
        "/api/v1/master-data/sync/auto", json={"enabled": False, "extra": True}
    ).status_code == 422
    assert "/admin/sync/master" not in app.openapi()["paths"]


def test_master_sync_conflict_returns_409(container, monkeypatch):
    container.settings.admin_enabled = True

    @contextmanager
    def busy_sync():
        yield False

    monkeypatch.setattr(container.uow, "sync_lock", busy_sync)
    with TestClient(create_app(container)) as client:
        response = client.post("/api/v1/master-data/sync", json={"full": False})
    assert response.status_code == 409


def test_auto_sync_api_configures_individual_streams_and_all(container):
    container.settings.admin_enabled = True
    container.auto_sync.environment_supported = True
    container.auto_sync.source_health = lambda: None
    container.sync.run(full=True)
    container.auto_sync.heartbeat_and_claim("worker-1")

    with TestClient(create_app(container)) as client:
        response = client.patch(
            "/api/v1/master-data/sync/auto",
            json={"enabled": True, "stream": "resources"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["auto_enabled"] is True
        assert body["auto_all_enabled"] is False
        assert [item["stream_name"] for item in body["auto_streams"] if item["enabled"]] == [
            "resources"
        ]

        response = client.patch(
            "/api/v1/master-data/sync/auto",
            json={"enabled": True, "stream": "all"},
        )
        assert response.status_code == 200
        assert response.json()["auto_all_enabled"] is True


def test_system_capabilities(container):
    
    with TestClient(create_app(container)) as client:
        response = client.get(
            '/api/v1/system/capabilities'
        )

    assert response.status_code == 200
    data = response.json()

    assert data['api_version'] == 'v1'
    assert data['formats'][0]['extension'] == '.pdf'
    assert data['formats'][0]['native_text_extraction'] is True
    assert data['master_sync']['source'] == 'json_fixture'
    assert data['master_sync']['auto_sync_supported'] is False
    assert data['processing']['scanned_pdf_ocr'] is False
    assert data['processing']['fake_ocr_demo'] is True
    assert(
        data['processing']['chunking']['size_words']
        == container.settings.chunk_size_words
    )
    assert data['processing']['chunking']['enabled'] is True
    assert data['representations']['bag_of_words'] is True
    assert data['representations']['vector']['enabled'] is False
    assert data['representations']['vector']['semantic'] is False
    assert data['retrieval']['modes'] == []
    assert data['storage']['mongodb_enabled'] is False
    assert 'database_url' not in response.text

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
    assert len(inspect(container.engine).get_table_names()) == len(Base.metadata.tables) + 1
    assert "sync_mode" in {
        column["name"] for column in inspect(container.engine).get_columns("sync_runs")
    }
    assert len(inspect(container.engine).get_foreign_keys("ocr_page_tasks")) == 1


def test_cli_demo_and_rerun(container, monkeypatch):
    from conftest import local_fixture_sync
    monkeypatch.setattr(container.sync, 'run', local_fixture_sync(container, container.sync.run))
    assert parser().parse_args(["sync-master", "--full"]).full
    assert parser().parse_args(
        ["run-master-sync-worker", "--worker-id", "worker-1", "--once"]
    ).once
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
