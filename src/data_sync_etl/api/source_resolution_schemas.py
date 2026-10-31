from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceResolutionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    master_resource_id: str
    version_id: str | None = None
    title: str | None = None
    resource_type_code: str | None = None
    source_label: Literal['LOCAL', 'SOURCE', 'UNKNOWN']
    source_identity: str | None = None
    resolution_status: Literal['available', 'unavailable', 'stale', 'blocked']
    content_availability: Literal[
        'metadata-only', 'remote-readable', 'ocr-processing', 'content-ready',
        'authorized-indexed', 'stale', 'revoked', 'unsupported',
    ]
    source_record_available: bool


class SourceOpenResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    master_resource_id: str
    version_id: str | None = None
    status: Literal[
        'EXTERNAL_VIEWER_AVAILABLE', 'SOURCE_AUTH_REQUIRED', 'METADATA_ONLY',
        'SOURCE_UNAVAILABLE', 'SOURCE_OPEN_UNSUPPORTED', 'VERSION_STALE',
        'ACCESS_DENIED',
    ]
    files: list['SourceFileReferenceResponse'] = Field(default_factory=list, max_length=100)


class SourceFileReferenceResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    source_file_id: str
    file_name: str | None = None
    mime_type: str | None = None
    revision: str | None = None
    access_status: Literal['ALLOWED', 'AUTH_REQUIRED', 'DENIED', 'UNAVAILABLE', 'METADATA_ONLY']
    viewer_url: str | None = None
    embedded_url: str | None = None
