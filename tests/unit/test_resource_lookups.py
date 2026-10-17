import io
from datetime import UTC, datetime

import pymupdf

from data_sync_etl.main import create_app
from tests.local_admin_client import TestClient


def pdf_bytes() -> bytes:
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "Catalog integration")
        return pdf.tobytes()


def test_resource_type_and_provider_catalogs_return_active_master_data(container):
    container.sync.run(full=True)

    with TestClient(create_app(container)) as client:
        resource_types = client.get("/api/v1/resource-types", params={"page": 1, "size": 1})
        providers = client.get("/api/v1/providers", params={"q": "publisher"})
        grade_levels = client.get("/api/v1/grade-levels", params={"q": "1"})

    assert resource_types.status_code == 200
    assert resource_types.json()["pagination"] == {
        "page": 1,
        "size": 1,
        "total": 2,
        "total_pages": 2,
    }
    assert len(resource_types.json()["items"]) == 1

    assert providers.status_code == 200
    assert providers.json()["items"] == [
        {
            "id": "provider-2",
            "name": "Demo Publisher",
            "provider_type": "PUBLISHER",
        }
    ]
    assert grade_levels.status_code == 200
    assert grade_levels.json()["items"] == [
        {
            "id": "grade-1",
            "grade_code": "G1",
            "grade_name_vi": "Lop 1",
        }
    ]


def test_resource_type_and_provider_catalogs_return_empty_envelopes(container):
    with TestClient(create_app(container)) as client:
        resource_types = client.get("/api/v1/resource-types")
        providers = client.get("/api/v1/providers")
        grade_levels = client.get("/api/v1/grade-levels")

    assert resource_types.json()["items"] == []
    assert resource_types.json()["pagination"]["total"] == 0
    assert providers.json()["items"] == []
    assert providers.json()["pagination"]["total_pages"] == 0
    assert grade_levels.json()["items"] == []


def test_resource_type_and_provider_catalogs_exclude_inactive_metadata(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update(
            "master_learning_resources",
            "resource-2",
            {
                "provider_is_active": False,
                "resource_type_is_active": False,
            },
        )

    with TestClient(create_app(container)) as client:
        resource_types = client.get("/api/v1/resource-types").json()["items"]
        providers = client.get("/api/v1/providers").json()["items"]

    assert all(item["code"] != "TEXTBOOK" for item in resource_types)
    assert all(item["id"] != "provider-1" for item in providers)


def test_catalog_keeps_active_identity_when_one_snapshot_is_stale(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.update(
            "master_learning_resources",
            "resource-3",
            {
                "resource_type_id": "stale-type",
                "resource_type_name_vi": "Legacy name",
                "resource_type_updated_at": datetime(2025, 1, 1, tzinfo=UTC),
            },
        )

    with TestClient(create_app(container)) as client:
        resource_types = client.get("/api/v1/resource-types").json()["items"]

    assert {item["code"] for item in resource_types} == {"HANDOUT", "TEXTBOOK"}
    assert next(item for item in resource_types if item["code"] == "HANDOUT")["name"] == "Tai lieu"


def test_catalog_values_are_accepted_by_create_resource(container):
    container.settings.admin_enabled = True
    container.sync.run(full=True)

    with TestClient(create_app(container), client=("127.0.0.1", 50000)) as client:
        resource_type = client.get("/api/v1/resource-types").json()["items"][0]
        provider = client.get("/api/v1/providers").json()["items"][0]
        response = client.post(
            "/api/v1/resources",
            data={
                "title": "Resource created from catalog",
                "resource_type_code": resource_type["code"],
                "provider_id": provider["id"],
                "provider_name": provider["name"],
                "provider_type": provider["provider_type"],
            },
            files={"file": ("catalog.pdf", io.BytesIO(pdf_bytes()), "application/pdf")},
        )

    assert response.status_code == 201, response.text
    assert response.json()["resource_type_code"] == resource_type["code"]


def test_catalog_routes_validate_pagination_and_have_business_tag(container):
    app = create_app(container)
    with TestClient(app) as client:
        assert client.get("/api/v1/resource-types", params={"page": 0}).status_code == 422
        assert client.get("/api/v1/providers", params={"size": 101}).status_code == 422
        assert client.get("/api/v1/grade-levels", params={"page": 0}).status_code == 422

    schema = app.openapi()
    expected_tag = ["Learning Resource Ingestion and Management"]
    assert schema["paths"]["/api/v1/resource-types"]["get"]["tags"] == expected_tag
    assert schema["paths"]["/api/v1/providers"]["get"]["tags"] == expected_tag
    assert schema["paths"]["/api/v1/grade-levels"]["get"]["tags"] == expected_tag
