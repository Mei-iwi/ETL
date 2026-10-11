from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class UpdateResourceMetadataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=500)
    resource_type_code: str | None = Field(default=None, max_length=50)
    resource_type_name_vi: str | None = Field(default=None, max_length=150)
    provider_id: str | None = Field(default=None, max_length=30)
    provider_name: str | None = Field(default=None, max_length=255)
    provider_type: str | None = Field(default=None, max_length=30)
    subject_id: str | None = Field(default=None, max_length=30)
    grade_level_id: str | None = Field(default=None, max_length=30)
    publication_status: Literal["DRAFT", "PUBLISHED", "UNPUBLISHED"] | None = None
    uploaded_by: str | None = Field(default=None, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def require_at_least_one_field(cls, value):
        if not isinstance(value, dict) or not value:
            raise ValueError("At least one metadata field is required")
        return value


class UpdateResourceMetadataResponse(BaseModel):
    id: str
    title: str
    resource_type_code: str
    resource_type_name_vi: str
    provider_id: str
    provider_name: str
    provider_type: str
    subject_id: str | None
    grade_level_id: str | None
    publication_status: str
    uploaded_by: str | None
    updated_at: datetime
