from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from data_sync_etl.application.update_resource_metadata import (
    InvalidMetadata,
    ResourceNotFound,
    UpdateResourceMetadataUseCase,
)
from data_sync_etl.domain.resource_metadata import UpdateResourceMetadataCommand
from data_sync_etl.main import create_app


class Repo:
    def __init__(self):
        self.row={"id":"r1","source_origin":"LOCAL","title":"Old","resource_type_code":"HANDOUT","resource_type_name_vi":"Tài liệu",
        "provider_id":"p1","provider_name":"Demo","provider_type":"SCHOOL","publication_status":"DRAFT",
        "created_by":None,"updated_at":None}
        self.subject=None
        self.grade=None
    def get_resource_for_update(self,rid): return self.row if rid=="r1" else None
    def get(self,table,key): return None
    def update_resource_metadata(self,rid,values):
        self.row.update({k:v for k,v in values.items() if k not in {"subject_id","grade_level_id"}})
        return self.row
    def replace_subject_relation(self,rid,sid): self.subject=sid
    def replace_grade_relation(self,rid,gid): self.grade=gid
    def get_resource_subject_id(self,rid): return self.subject
    def get_resource_grade_level_id(self,rid): return self.grade
class Uow:
    def __init__(self,repo): self.repo=repo
    def __call__(self): return self
    def __enter__(self): return self.repo
    def __exit__(self,*args): return False

def test_patch_title_only():
    repo=Repo()
    use=UpdateResourceMetadataUseCase(Uow(repo))
    out=use.execute(UpdateResourceMetadataCommand(resource_id="r1",title=" New ",supplied_fields=frozenset({"title"})))
    assert out["title"]=="New" and repo.row["resource_type_code"]=="HANDOUT"

def test_empty_patch_rejected():
    with pytest.raises(InvalidMetadata):
        UpdateResourceMetadataUseCase(Uow(Repo())).execute(UpdateResourceMetadataCommand(resource_id="r1"))

def test_missing_resource():
    with pytest.raises(ResourceNotFound):
        UpdateResourceMetadataUseCase(Uow(Repo())).execute(UpdateResourceMetadataCommand(resource_id="missing",title="X",supplied_fields=frozenset({"title"})))


def test_patch_metadata_route(container):
    container.sync.run(full=True)
    with container.uow() as repo:
        repo.insert("master_learning_resources", {
            "id": "resource-patch", "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC), "provider_id": "provider-1",
            "provider_name": "Demo School", "provider_type": "SCHOOL",
            "provider_is_active": True, "resource_type_id": "type-patch",
            "resource_type_code": "HANDOUT", "resource_type_name_vi": "Tai lieu",
            "resource_type_is_active": True, "created_by": None,
            "title": "Old title", "publication_status": "DRAFT", "is_active": True,
            "source_origin": "LOCAL",
        })
        repo.insert("resource_subjects", {
            "resource_id": "resource-patch", "subject_id": "subject-1", "is_primary": True,
        })
        repo.insert("resource_grade_levels", {
            "resource_id": "resource-patch", "grade_level_id": "grade-1",
        })

    with TestClient(create_app(container)) as client:
        response = client.patch(
            "/api/v1/resources/resource-patch/metadata",
            json={"title": "New title", "publication_status": "PUBLISHED"},
        )

    assert response.status_code == 200
    assert response.json()["title"] == "New title"
    assert response.json()["publication_status"] == "PUBLISHED"
    assert response.json()["subject_id"] == "subject-1"
    assert response.json()["grade_level_id"] == "grade-1"
