"""Explicit MASTER-to-Mongo resource extensions; no SOURCE or relational writes."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import PyMongoError

from data_sync_etl.adapters.mongo.foundation_store import ProfileConflict, user_profile_revision
from data_sync_etl.api.admin_access import require_local_admin
from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.mongo_documents import normalize_keywords
from data_sync_etl.security.auth import AuthError
from data_sync_etl.security.authorization import owns_resource

router = APIRouter(prefix='/api/v1/resources', tags=['Resource description'])
ResourceId = Annotated[str, Path(min_length=1, max_length=30)]


class AuthorInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    name: str = Field(min_length=1, max_length=255)
    organization: str | None = Field(default=None, max_length=255)
    role: str | None = Field(default=None, max_length=255)

    @field_validator('name', 'organization', 'role')
    @classmethod
    def trim(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator('name')
    @classmethod
    def required_name(cls, value):
        if not value:
            raise ValueError('Author name required')
        return value


class DescriptionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    revision: str = Field(pattern=r'^(missing|[a-f0-9]{64})$')
    description: str | None = Field(default=None, max_length=10000)
    authors: list[AuthorInput] | None = Field(default=None, max_length=50)
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] | None = Field(default=None, max_length=50)

    @field_validator('description')
    @classmethod
    def trim(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value

    @field_validator('tags')
    @classmethod
    def normalize(cls, value):
        return normalize_keywords(value) if value is not None else None


class DescriptionOutput(BaseModel):
    resource_id: str
    description: str | None = None
    authors: list[dict] | None = None
    tags: list[str] | None = None
    updated_at: datetime | None = None
    revision: str
    editable: bool
    unavailable_reason: str | None = None


def target(request, resource_id):
    container = require_local_admin(request)
    with container.uow() as repo:
        row = repo.get('master_learning_resources', resource_id)
    if not row or not row['is_active'] or row.get('deleted_at') or row.get('source_missing_at'):
        raise HTTPException(404, 'Resource not found')
    principal = getattr(request.state, 'auth_principal', None)
    editable = container.settings.admin_enabled and (
        (principal is None and container.settings.auth_mode == 'legacy_local')
        or (principal is not None and (principal.role == 'ADMIN'
            or (principal.mode == 'master_users' and owns_resource(principal, row)))))
    return container, row.get('mongo_id'), bool(editable)


def output(container, resource_id, mongo_id, editable):
    base = dict(resource_id=resource_id, revision='missing', editable=False)
    if not mongo_id:
        return {**base, 'unavailable_reason': 'RESOURCE_DESCRIPTION_NOT_LINKED'}
    metadata = getattr(container, 'metadata_documents', None)
    if metadata is None:
        raise AuthError('RESOURCE_DESCRIPTION_UNAVAILABLE', 503)
    try:
        document = metadata.get_resource(mongo_id)
        values = document.model_dump(include={'description', 'authors', 'tags', 'updated_at'}) if document else {}
        return {**base, **values, 'editable': editable, 'revision': user_profile_revision(document)}
    except (DomainError, PyMongoError):
        raise AuthError('RESOURCE_DESCRIPTION_UNAVAILABLE', 503) from None


@router.get('/{resource_id}/description', response_model=DescriptionOutput)
def get_description(resource_id: ResourceId, request: Request):
    container, mongo_id, editable = target(request, resource_id)
    return output(container, resource_id, mongo_id, editable)


@router.patch('/{resource_id}/description', response_model=DescriptionOutput)
def update_description(resource_id: ResourceId, body: DescriptionInput, request: Request):
    container, mongo_id, editable = target(request, resource_id)
    if not editable:
        raise AuthError('AUTH_RESOURCE_FORBIDDEN', 403)
    if not mongo_id:
        raise AuthError('RESOURCE_DESCRIPTION_NOT_LINKED', 409)
    changes = body.model_dump(exclude_unset=True, exclude={'revision'})
    if not changes:
        raise AuthError('RESOURCE_DESCRIPTION_INPUT_INVALID', 422)
    metadata = getattr(container, 'metadata_documents', None)
    if metadata is None:
        raise AuthError('RESOURCE_DESCRIPTION_UNAVAILABLE', 503)
    try:
        metadata.update_resource_description(mongo_id, changes, body.revision)
    except ProfileConflict:
        raise AuthError('RESOURCE_DESCRIPTION_CHANGED', 409) from None
    except (DomainError, PyMongoError):
        raise AuthError('RESOURCE_DESCRIPTION_UNAVAILABLE', 503) from None
    return output(container, resource_id, mongo_id, editable)
