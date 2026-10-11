from datetime import datetime

from pydantic import BaseModel, Field


class ResourceListItem(BaseModel):
    id: str
    title: str
    provider_name: str
    resource_type_code: str
    resource_type_name_vi: str
    publication_status: str
    updated_at: datetime


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
    title: str
    provider_id: str
    provider_name: str
    provider_type: str
    resource_type_code: str
    resource_type_name_vi: str
    publication_status: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    subjects: list[SubjectBrief]
    grade_levels: list[GradeLevelBrief]