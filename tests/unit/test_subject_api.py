from fastapi.testclient import TestClient

from data_sync_etl.main import create_app


def test_list_subjects_returns_active_subjects(container):
    container.sync.run("grades", full=True)
    container.sync.run("subjects", full=True)

    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/subjects", params={"page": 1, "size": 2})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pagination"] == {"page": 1, "size": 2, "total": 3, "total_pages": 2}
    assert len(body["items"]) == 2
    assert {item["id"] for item in body["items"]} <= {"subject-1", "subject-2", "subject-3"}


def test_list_subjects_filters_by_keyword_and_grade(container):
    container.sync.run("grades", full=True)
    container.sync.run("subjects", full=True)

    with TestClient(create_app(container)) as client:
        response = client.get(
            "/api/v1/subjects",
            params={"q": "Toan", "grade_level_id": "grade-1"},
        )

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["items"]] == ["subject-1"]


def test_list_subjects_returns_message_when_no_subject_matches(container):
    container.sync.run("grades", full=True)
    container.sync.run("subjects", full=True)

    with TestClient(create_app(container)) as client:
        response = client.get(
            "/api/v1/subjects",
            params={"grade_level_id": "grade-does-not-exist"},
        )

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["message"] == "Không có môn học nào"


def test_recommended_subjects_respects_grade_and_exclusions(container):
    container.sync.run(full=True)

    with TestClient(create_app(container)) as client:
        response = client.get(
            "/api/v1/subjects/recommended",
            params={"grade_level_id": "grade-1", "exclude_ids": "subject-1"},
        )

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert all(item["id"] != "subject-1" for item in items)
    assert all("reason" in item and "score" in item for item in items)


def test_subject_routes_validate_query_limits(container):
    with TestClient(create_app(container)) as client:
        response = client.get("/api/v1/subjects", params={"page": 0})
        recommended = client.get("/api/v1/subjects/recommended", params={"limit": 51})

    assert response.status_code == 422
    assert recommended.status_code == 422
