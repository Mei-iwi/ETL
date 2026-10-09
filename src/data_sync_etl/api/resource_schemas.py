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


class CreateResourceResponse(BaseModel):
    id: str
    title: str
    resource_type_code: str
    media_category: str
    mime_type: str
    file_name: str
    file_size: int
    source_hash: str
    initial_version_id: str
    version_number: int
    lifecycle_status: str
    created_at: datetime