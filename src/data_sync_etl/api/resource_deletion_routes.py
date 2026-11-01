"""Authenticated admin-only lifecycle commands; never write SOURCE."""
import hashlib
from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, ConfigDict, Field

from data_sync_etl.application.resource_deletion import ResourceDeletion
from data_sync_etl.application.source_resource_delete import SourceDeleteWriteBackError
from data_sync_etl.security.auth import AuthError

router = APIRouter(prefix='/api/v1/resources', tags=['Master data'])
ResourceId = Annotated[str, Path(min_length=1, max_length=30)]


class DeletionCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reason: str | None = Field(default=None, max_length=500)
    expected_revision: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=100,
                                        pattern=r'^[A-Za-z0-9._:-]+$')


def command(request, resource_id, body, restore):
    container = request.app.state.container
    if not container.settings.admin_enabled:
        raise AuthError('AUTH_ADMIN_DISABLED', 403)
    principal = getattr(request.state, 'auth_principal', None)
    if (not restore and container.settings.source_metadata_writeback_enabled
            and principal is not None):
        with container.uow() as repo:
            resource = repo.get('master_learning_resources', resource_id)
        if resource is not None and resource.get('source_origin') == 'POSTGRESQL':
            key = body.idempotency_key or ('web-delete-' + hashlib.sha256(
                repr((resource_id, resource.get('source_state_hash'), principal.id)).encode()
            ).hexdigest()[:48])
            try:
                result = container.source_resource_delete.request(
                    resource_id,
                    expected_source_state_hash=resource.get('source_state_hash') or '',
                    idempotency_key=key,
                    actor_id=principal.id,
                    role=principal.role,
                    approve=False,
                )
                # No local tombstone is written before the SOURCE job commits.
                return result
            except SourceDeleteWriteBackError as exc:
                raise AuthError(exc.code, exc.status) from None
    return ResourceDeletion(container.uow).change(resource_id,
        getattr(request.state, 'auth_principal', None), restore=restore,
        reason=body.reason, expected_revision=body.expected_revision)


@router.delete('/{resource_id}')
def delete_resource(resource_id: ResourceId, request: Request, body: DeletionCommand | None = None):
    return command(request, resource_id, body or DeletionCommand(), False)


@router.post('/{resource_id}/restore')
def restore_resource(resource_id: ResourceId, request: Request, body: DeletionCommand | None = None):
    return command(request, resource_id, body or DeletionCommand(), True)
