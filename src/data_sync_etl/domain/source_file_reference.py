import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SourceFileReference(BaseModel):
    """A file identity returned by SOURCE without exposing a storage locator."""

    model_config = ConfigDict(extra='forbid', frozen=True, hide_input_in_errors=True)

    source_resource_id: str = Field(min_length=1, max_length=255, repr=False)
    source_version_id: str = Field(min_length=1, max_length=30, repr=False)
    source_file_id: str = Field(min_length=1, max_length=255)
    file_name: str | None = Field(default=None, max_length=255)
    mime_type: str | None = Field(default=None, max_length=127)
    revision: str | None = Field(default=None, max_length=255)
    access_status: Literal['ALLOWED', 'AUTH_REQUIRED', 'DENIED', 'UNAVAILABLE', 'METADATA_ONLY']
    viewer_url: str | None = Field(default=None, max_length=2048, repr=False)
    embedded_url: str | None = Field(default=None, max_length=2048, repr=False)

    @field_validator('file_name')
    @classmethod
    def filename_is_not_a_locator(cls, value):
        if value is not None and (value != value.strip() or '/' in value or '\\' in value
                                  or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError('file_name must be a display name, not a path')
        return value
    @field_validator('source_file_id')
    @classmethod
    def file_id_is_opaque(cls, value):
        if (value != value.strip() or '://' in value
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError('source_file_id must be an opaque identifier')
        return value

    @field_validator('revision')
    @classmethod
    def revision_is_opaque(cls, value):
        if value is not None and (value != value.strip() or '://' in value
                                  or re.search(r'(?:password|secret|token|signature)\s*[=:]', value, re.I)
                                  or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError('revision must be an opaque non-secret value')
        return value
