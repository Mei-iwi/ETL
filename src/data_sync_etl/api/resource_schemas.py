from datetime import datetime

from pydantic import BaseModel, Field


class ResourceVersionSummary(BaseModel):
    id: str
    version_number: int
    lifecycle_status: str
    processing_status: str
    created_at: datetime
    source_version_id: str | None = None


class ResourceListItem(BaseModel):
    id: str
    title: str | None
    provider_name: str | None
    resource_type_code: str
    resource_type_name_vi: str
    publication_status: str
    updated_at: datetime
    latest_version: ResourceVersionSummary | None = None
    deletion_revision: int = 0
    master_deleted_at: datetime | None = None


class ResourcePagination(BaseModel):
    page: int = Field(ge=1)
    size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)

class ResouceListResponse(BaseModel):
    items: list[ResourceListItem]
    pagination: ResourcePagination

class SubjectBrief(BaseModel):
    id: str
    name: str
    is_primary: bool

class GradeLevelBrief(BaseModel):
    id: str
    grade_code: str
    grade_name_vi: str

class ResourceDetailResponse(BaseModel):
    id: str
    deletion_revision: int = 0
    source_origin: str | None = None
    title: str | None
    provider_id: str | None
    provider_name: str | None
    provider_type: str | None
    resource_type_code: str
    resource_type_name_vi: str
    publication_status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    subjects: list[SubjectBrief]
    grade_levels: list[GradeLevelBrief]
