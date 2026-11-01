from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceWriteBackRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    title: str | None = Field(default=None, max_length=500)
    provider_id: str | None = Field(default=None, max_length=30)
    resource_type_id: str | None = Field(default=None, max_length=30)
    publication_status: Literal['DRAFT', 'PUBLISHED', 'UNPUBLISHED', 'ARCHIVED'] | None = None
    expected_source_state_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-fA-F]{64}$')
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9._:-]+$')
    approve: bool = False
    dry_run: bool = False


class SourceWriteBackResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str | None
    status: Literal['DRY_RUN', 'WRITEBACK_PENDING_APPROVAL', 'WRITEBACK_PENDING',
                    'WRITEBACK_PROCESSING', 'WRITEBACK_APPLIED', 'WRITEBACK_CONFLICT',
                    'WRITEBACK_FAILED']
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
    create_supported: bool = False
    actor_mapping: str | None = None
    version_create_supported: bool = False
    delete_supported: bool = False
    delete_mode: str | None = None
    cloud_upload_supported: bool = False
    version_storage: str | None = None
    auto_approval: bool = False
    # Exposed only by the resource-scoped status route to support optimistic
    # concurrency in the frontend.  It is not a credential or source locator.
    expected_source_state_hash: str | None = None


class SourceCreateResourceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    provider_id: str = Field(min_length=1, max_length=30)
    resource_type_id: str = Field(min_length=1, max_length=30)
    title: str = Field(min_length=1, max_length=500)
    publication_status: Literal['DRAFT', 'PUBLISHED', 'ARCHIVED'] = 'DRAFT'
    subject_id: str | None = Field(default=None, max_length=30)
    grade_level_id: str | None = Field(default=None, max_length=30)
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9._:-]+$')


class SourceCreateResourceResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str
    status: Literal['SOURCE_CREATE_PENDING', 'SOURCE_CREATE_PROCESSING',
                    'SOURCE_CREATE_APPLIED', 'SOURCE_CREATE_FAILED']
    operation: Literal['CREATE_RESOURCE']
    source_resource_id: str | None = None
    source_version_id: str | None = None
    master_resource_id: str | None = None
    workflow: str = 'SOURCE_RESOURCE_PENDING'
    source_commit_status: Literal['PENDING', 'SOURCE_COMMITTED'] = 'PENDING'
    master_reconciliation_status: Literal['NOT_STARTED', 'PENDING_RECONCILIATION', 'MASTER_RECONCILED'] = 'NOT_STARTED'
    reconciliation_required: bool = False
    error_code: str | None = None


class SourceDeleteResourceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    expected_source_state_hash: str = Field(min_length=64, max_length=64, pattern=r'^[0-9a-fA-F]{64}$')
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r'^[A-Za-z0-9._:-]+$')
    approve: bool = False


class SourceDeleteResourceResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str
    status: Literal['SOURCE_DELETE_PENDING', 'SOURCE_DELETE_PROCESSING',
                    'SOURCE_DELETE_APPLIED', 'SOURCE_DELETE_FAILED']
    operation: Literal['DELETE_RESOURCE']
    resource_id: str
    reconciliation_required: bool = False
    error_code: str | None = None


class SourceCreateVersionResponse(BaseModel):
    model_config = ConfigDict(extra='forbid')

    request_id: str
    status: Literal['SOURCE_VERSION_PENDING', 'SOURCE_VERSION_PROCESSING',
                    'SOURCE_VERSION_APPLIED', 'SOURCE_VERSION_FAILED']
    operation: Literal['CREATE_VERSION']
    resource_id: str
    source_version_id: str | None = None
    workflow: str
    reconciliation_required: bool = False
    error_code: str | None = None
