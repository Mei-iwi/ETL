from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceWriteBackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str | None = Field(default=None, max_length=500)
    expected_source_state_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-fA-F]{64}$')
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9._:-]+$')
    approve: bool = False
    dry_run: bool = False


class SourceWriteBackResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str | None
    status: Literal['DRY_RUN', 'WRITEBACK_PENDING_APPROVAL', 'WRITEBACK_PENDING',
                    'WRITEBACK_APPLIED', 'WRITEBACK_CONFLICT', 'WRITEBACK_FAILED']
    resource_id: str
    fields: list[str]
    writer_supported: bool
    error_code: str | None = None


class SourceWriteBackStatusResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    enabled: bool
    writer_supported: bool
    mode: str
    fields: list[str]
    default: Literal['disabled']
