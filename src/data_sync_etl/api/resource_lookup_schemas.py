from pydantic import BaseModel

from data_sync_etl.api.resource_schemas import ResourcePagination


class CatalogMetadata(BaseModel):
    identity_overlaps: int = 0
    source_status: str
    completeness: str


class ResourceTypeItem(BaseModel):
    code: str
    name: str


class ResourceTypeListResponse(BaseModel):
    items: list[ResourceTypeItem]
    pagination: ResourcePagination
    catalog: CatalogMetadata | None = None


class ProviderItem(BaseModel):
    id: str
    name: str
    provider_type: str


class ProviderListResponse(BaseModel):
    items: list[ProviderItem]
    pagination: ResourcePagination
    catalog: CatalogMetadata | None = None


class GradeLevelItem(BaseModel):
    id: str
    grade_code: str
    grade_name_vi: str


class GradeLevelListResponse(BaseModel):
    items: list[GradeLevelItem]
    pagination: ResourcePagination
    catalog: CatalogMetadata | None = None
