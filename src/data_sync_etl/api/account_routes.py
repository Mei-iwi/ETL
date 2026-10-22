"""Admin-only account lifecycle; never serializes a password hash."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from data_sync_etl.api.auth_routes import service
from data_sync_etl.security.auth import AuthError

router = APIRouter(prefix='/api/v1/admin/users', tags=['Account administration'])
Role = Literal['admin', 'operator', 'viewer']
Capability = Literal['sync', 'process', 'index', 'evaluation']


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', hide_input_in_errors=True)


class RolesInput(Input):
    roles: list[Role] = Field(min_length=1, max_length=3)


class CreateInput(RolesInput):
    email: str = Field(min_length=3, max_length=254)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=15, max_length=1024, repr=False)
    permissions: list[Capability] = Field(default_factory=list, max_length=4)
    resource_ids: list[str] = Field(default_factory=list, max_length=1000)


class UpdateInput(Input):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    is_active: bool | None = None
    is_locked: bool | None = None
    permissions: list[Capability] | None = Field(default=None, max_length=4)
    resource_ids: list[str] | None = Field(default=None, max_length=1000)


class PasswordInput(Input):
    password: str = Field(min_length=15, max_length=1024, repr=False)


def administrator(request):
    auth = service(request)
    if auth.settings.auth_mode != 'db_users':
        raise AuthError('AUTH_DATABASE_MODE_REQUIRED', 403)
    principal = auth.principal(request)
    if principal.role != 'ADMIN' or not auth.settings.admin_enabled or principal.must_change_password:
        raise AuthError('AUTH_FORBIDDEN', 403)
    return auth, principal


def grants(values):
    if 'resource_ids' in values:
        if any(not value or len(value) > 30 or not value.isalnum() for value in values['resource_ids']):
            raise AuthError('AUTH_USER_INPUT_INVALID', 422)
        values['resource_ids'] = sorted(set(values['resource_ids']))
    if 'permissions' in values:
        values['permissions'] = sorted(set(values['permissions']))
    return values


@router.get('')
def list_users(request: Request, page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100)):
    auth, _ = administrator(request)
    items, total = auth.repository.list(page, size)
    return {'items': items, 'total': total, 'page': page, 'size': size}


@router.post('', status_code=201)
def create_user(body: CreateInput, request: Request):
    auth, actor = administrator(request)
    values = grants(body.model_dump(exclude={'password', 'roles', 'email', 'display_name'}))
    if body.permissions and 'operator' not in body.roles and 'admin' not in body.roles:
        raise AuthError('AUTH_USER_INPUT_INVALID', 422)
    result, _ = auth.repository.create(body.email, body.display_name.strip(), auth.hasher.hash(body.password),
                                       sorted(set(body.roles)), actor=actor, **values)
    return result


@router.patch('/{id}')
def update_user(id: UUID, body: UpdateInput, request: Request):
    auth, actor = administrator(request)
    values = body.model_dump(exclude_unset=True)
    if any(value is None for value in values.values()):
        raise AuthError('AUTH_USER_INPUT_INVALID', 422)
    result = auth.repository.change(str(id), grants(values), actor=actor)
    if str(id) == actor.id:
        request.state.auth_change_completed = True
    return result


@router.patch('/{id}/roles')
def change_roles(id: UUID, body: RolesInput, request: Request):
    auth, actor = administrator(request)
    values = {} if 'operator' in body.roles or 'admin' in body.roles else {'permissions': []}
    result = auth.repository.change(str(id), values, role_codes=sorted(set(body.roles)), actor=actor)
    if str(id) == actor.id:
        request.state.auth_change_completed = True
    return result


@router.post('/{id}/reset-password')
def reset_password(id: UUID, body: PasswordInput, request: Request):
    auth, actor = administrator(request)
    auth.repository.change(str(id), {'password_hash': auth.hasher.hash(body.password), 'must_change_password': True}, actor=actor)
    if str(id) == actor.id:
        request.state.auth_change_completed = True
    return {'reset': True, 'must_change_password': True}
