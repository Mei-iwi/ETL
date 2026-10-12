from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.domain.resource_version import (
    CreateResourceVersionCommand,
    CreateResourceVersionResult,
)
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.processing import StoragePort
from data_sync_etl.ports.repositories import UnitOfWork


class ResourceVersionNotFound(DomainError):
    pass


class InvalidResourceVersion(DomainError):
    def __init__(self, message: str, status_code: int = 400):
        self.status_code = status_code
        super().__init__(message)


class ResourceVersionPersistenceError(DomainError):
    pass


class CreateResourceVersionUseCase:
    def __init__(self, uow: UnitOfWork, storage: StoragePort, format_registry: FormatValidatorRegistry):
        self.uow = uow
        self.storage = storage
        self.format_registry = format_registry

    def execute(self, command: CreateResourceVersionCommand) -> CreateResourceVersionResult:
        try:
            command.validate()
            validator = self.format_registry.find_validator(command.filename)
            command.file_stream.seek(0)
            header = command.file_stream.read(16)
            command.file_stream.seek(0)
            validator.validate(
                validator.extension,
                command.declared_mime,
                header,
                command.file_size,
            )
        except DomainError as exc:
            raise InvalidResourceVersion(str(exc)) from None

        try:
            stored = self.storage.save_stream(
                command.file_stream,
                max_size_bytes=validator.max_size_bytes,
                suffix=validator.extension,
            )
        except DomainError as exc:
            raise InvalidResourceVersion(str(exc)) from None
        except Exception as exc:
            raise ResourceVersionPersistenceError("Unable to store resource version file") from exc

        try:
            with flow():
                with self.uow() as repo:
                    resource = repo.get(
                        "master_learning_resources", command.resource_id, lock=True
                    )
                    if resource is None:
                        raise ResourceVersionNotFound("Resource not found")
                    if not resource["is_active"] or resource["deleted_at"] is not None:
                        raise InvalidResourceVersion("Resource is inactive or deleted", 409)

                    previous = repo.find(
                        "resource_versions",
                        {"resource_id": command.resource_id},
                        order=("-version_number",),
                        limit=1,
                    )
                    latest = previous[0] if previous else None
                    if latest and latest["source_hash"] == stored.source_hash:
                        result = self._result(
                            "NO_CHANGE", command, latest, command.declared_mime
                        )
                    else:
                        stamp = now()
                        version = repo.insert(
                            "resource_versions",
                            {
                                "id": new_id(),
                                "created_at": stamp,
                                "updated_at": stamp,
                                "resource_id": command.resource_id,
                                "parent_version_id": latest["id"] if latest else None,
                                "version_number": latest["version_number"] + 1 if latest else 1,
                                "lifecycle_status": "INGESTED",
                                "storage_bucket": stored.bucket,
                                "storage_object_key": stored.object_key,
                                "source_hash": stored.source_hash,
                                "uploaded_by": command.uploaded_by.strip() if command.uploaded_by else None,
                            },
                        )
                        result = self._result("CREATED", command, version, command.declared_mime)

            if result.action == "NO_CHANGE" and stored.created:
                self.storage.delete(stored.bucket, stored.object_key)
            event(
                resource_id=command.resource_id,
                resource_version_id=result.resource_version_id,
                stage="ingest",
                action=result.action,
            )
            return result
        except (ResourceVersionNotFound, InvalidResourceVersion):
            if stored.created:
                self.storage.delete(stored.bucket, stored.object_key)
            raise
        except Exception as exc:
            if stored.created:
                self.storage.delete(stored.bucket, stored.object_key)
            raise ResourceVersionPersistenceError(
                "Database rejected the resource version"
            ) from exc

    @staticmethod
    def _result(action, command, version, declared_mime):
        return CreateResourceVersionResult(
            action=action,
            resource_id=version["resource_id"],
            resource_version_id=version["id"],
            parent_version_id=version["parent_version_id"],
            version_number=version["version_number"],
            lifecycle_status=version["lifecycle_status"],
            file_name=command.filename,
            file_size=command.file_size,
            mime_type=declared_mime.lower().split(";", 1)[0].strip(),
            source_hash=version["source_hash"],
            uploaded_by=version.get("uploaded_by"),
            created_at=version["created_at"],
        )
