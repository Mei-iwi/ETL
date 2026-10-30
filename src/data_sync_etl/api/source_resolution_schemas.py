from typing import Literal

from pydantic import BaseModel, ConfigDict


class SourceResolutionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    master_resource_id: str
    version_id: str | None = None
    title: str | None = None
    resource_type_code: str | None = None
    source_label: Literal['LOCAL', 'SOURCE', 'UNKNOWN']
    source_identity: str | None = None
    resolution_status: Literal['available', 'unavailable', 'stale', 'blocked']
    content_availability: Literal['metadata-only', 'authorized-indexed']
    source_record_available: bool
