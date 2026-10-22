"""Own profile only: synced identity is read-only; Mongo extension fields are editable."""

from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import PyMongoError

from data_sync_etl.adapters.mongo.foundation_store import ProfileConflict, user_profile_revision
from data_sync_etl.api.auth_routes import service
from data_sync_etl.domain.core import DomainError
from data_sync_etl.security.auth import AuthError

router = APIRouter(prefix='/api/v1/auth/profile', tags=['Personal profile'])


class ProfileInput(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)
    revision: str = Field(pattern=r'^(missing|[a-f0-9]{64})$')
    organization_name: str | None = Field(default=None, max_length=255)
    address: str | None = Field(default=None, max_length=1000)
    bio: str | None = Field(default=None, max_length=2000)

    @field_validator('organization_name', 'address', 'bio')
    @classmethod
    def trim(cls, value):
        return (value.strip() or None) if isinstance(value, str) else value


class ProfileOutput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    full_name: str
    email: str
    role_name: str
    access_role: str
    is_active: bool
    email_verified: bool | None = None
    created_at: datetime | None = None
    subject_name: str | None = None
    organization_name: str | None = None
    address: str | None = None
    bio: str | None = None
    updated_at: datetime | None = None
    revision: str
    editable: bool
    unavailable_reason: str | None = None


def identity(request):
    auth = service(request)
    principal = auth.principal(request)
    container = request.app.state.container
    base = dict(full_name=principal.display_name or ('Quản trị viên' if principal.role == 'ADMIN' else 'Người dùng'),
        email=principal.email or auth.settings.admin_login_email,
        role_name={'ADMIN': 'Quản trị viên', 'OPERATOR': 'Điều phối viên', 'VIEWER': 'Người dùng'}[principal.role],
        access_role=principal.role, is_active=True, revision='missing', editable=False)
    if not (principal.mode == 'master_users' and principal.provenance == 'master-readonly'
            and principal.id.startswith('master:')):
        return base, None
    with container.uow() as repo:
        user = repo.get('master_users', principal.id.removeprefix('master:'))
    if not user or not user['is_active'] or user.get('deleted_at') or user.get('source_missing_at'):
        raise AuthError('AUTH_SESSION_EXPIRED')
    base.update(full_name=user.get('full_name') or 'Người dùng', email=user['email'],
        role_name=user.get('role_name') or user['role_code'], email_verified=bool(user.get('email_verified_at')),
        created_at=user.get('created_at'))
    return base, user.get('mongo_id')


def profile(request, base, mongo_id):
    if not mongo_id:
        return {**base, 'unavailable_reason': 'PROFILE_NOT_LINKED'}
    metadata = getattr(request.app.state.container, 'metadata_documents', None)
    if metadata is None:
        raise AuthError('PROFILE_UNAVAILABLE', 503)
    try:
        document = metadata.get_user(mongo_id)
        details = {} if document is None else {
            name: getattr(document, name) for name in ('organization_name', 'address', 'bio', 'updated_at')}
        subject_name = None
        if document is not None and document.subject_id:
            with request.app.state.container.uow() as repo:
                subject = repo.get('subjects', document.subject_id)
            if subject and subject.get('is_active') and not subject.get('deleted_at'):
                subject_name = subject.get('subject_name')
        return {**base, **details, 'subject_name': subject_name, 'editable': True,
            'revision': user_profile_revision(document)}
    except (PyMongoError, DomainError):
        raise AuthError('PROFILE_UNAVAILABLE', 503) from None


@router.get('', response_model=ProfileOutput)
def get_profile(request: Request):
    base, mongo_id = identity(request)
    return profile(request, base, mongo_id)


@router.patch('', response_model=ProfileOutput)
def update_profile(body: ProfileInput, request: Request):
    base, mongo_id = identity(request)
    if not mongo_id:
        raise AuthError('PROFILE_NOT_LINKED', 403)
    values = body.model_dump(exclude_unset=True, exclude={'revision'})
    if not values:
        raise AuthError('PROFILE_INPUT_INVALID', 422)
    metadata = getattr(request.app.state.container, 'metadata_documents', None)
    if metadata is None:
        raise AuthError('PROFILE_UNAVAILABLE', 503)
    try:
        metadata.update_personal_profile(mongo_id, values, body.revision)
    except ProfileConflict:
        raise AuthError('PROFILE_CHANGED', 409) from None
    except (PyMongoError, DomainError):
        raise AuthError('PROFILE_UNAVAILABLE', 503) from None
    return profile(request, base, mongo_id)
