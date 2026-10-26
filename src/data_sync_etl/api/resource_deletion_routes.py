"""Authenticated admin-only lifecycle commands; never write SOURCE."""
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from data_sync_etl.application.resource_deletion import ResourceDeletion
from data_sync_etl.security.auth import AuthError

router = APIRouter(prefix='/api/v1/resources', tags=['Master data'])
ResourceId = Annotated[str, Path(min_length=1, max_length=30)]


class DeletionCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str | None = Field(default=None, max_length=500)
    expected_revision: int | None = Field(default=None, ge=0)


def command(request, resource_id, body, restore):
    container = request.app.state.container
    if not container.settings.admin_enabled:
        raise AuthError('AUTH_ADMIN_DISABLED', 403)
    return ResourceDeletion(container.uow).change(resource_id,
        getattr(request.state, 'auth_principal', None), restore=restore,
        reason=body.reason, expected_revision=body.expected_revision)


@router.delete('/{resource_id}')
def delete_resource(resource_id: ResourceId, request: Request, body: DeletionCommand | None = None):
    return command(request, resource_id, body or DeletionCommand(), False)


@router.post('/{resource_id}/restore')
def restore_resource(resource_id: ResourceId, request: Request, body: DeletionCommand | None = None):
    return command(request, resource_id, body or DeletionCommand(), True)
