from data_sync_etl.domain.core import (
    DomainError,
    ResourceCreateError,
    UploadTooLargeError,
    new_id,
    now,
)
from data_sync_etl.domain.format_validation import FormatValidatorRegistry, InvalidFileContent
from data_sync_etl.domain.resource import CreateResourceCommand, CreateResourceResult
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.processing import StoragePort
from data_sync_etl.ports.repositories import UnitOfWork


class CreateResourceUseCase:
    def __init__(
        self, uow: UnitOfWork, storage: StoragePort, format_registry: FormatValidatorRegistry
    ):
        self.uow = uow
        self.storage = storage
        self.format_registry = format_registry

    @staticmethod
    def _validate_metadata(repo, command: CreateResourceCommand) -> tuple[str, str]:
        if command.subject_id:
            subject = repo.get("subjects", command.subject_id)
            if not subject or not subject["is_active"] or subject["deleted_at"] is not None:
                raise DomainError("Subject not found or inactive")
            if command.grade_level_id and subject["grade_id"] not in (None, command.grade_level_id):
                raise DomainError("Subject does not belong to grade level")
        if command.grade_level_id:
            grade = repo.get("master_grade_levels", command.grade_level_id)
            if not grade or not grade["grade_is_active"] or grade["grade_deleted_at"] is not None:
                raise DomainError("Grade level not found or inactive")

        provider_id = command.provider_id.strip()
        provider_name = command.provider_name.strip()
        provider_type = command.provider_type.strip()
        for known in repo.find("master_learning_resources", {"provider_id": provider_id}):
            if (
                known["provider_name"] != provider_name
                or known["provider_type"] != provider_type
                or not known["provider_is_active"]
                or known["provider_deleted_at"] is not None
            ):
                raise DomainError("Provider metadata conflicts with known master data")

        type_code = command.resource_type_code.strip()
        known_types = repo.find("master_learning_resources", {"resource_type_code": type_code})
        if known_types:
            canonical = known_types[0]
            if any(
                known["resource_type_id"] != canonical["resource_type_id"]
                or known["resource_type_name_vi"] != canonical["resource_type_name_vi"]
                or not known["resource_type_is_active"]
                or known["resource_type_deleted_at"] is not None
                for known in known_types
            ):
                raise DomainError("Resource type master data is inconsistent")
            if (
                command.resource_type_name_vi is not None
                and command.resource_type_name_vi.strip() != canonical["resource_type_name_vi"]
            ):
                raise DomainError("Resource type metadata conflicts with known master data")
            return canonical["resource_type_id"], canonical["resource_type_name_vi"]
        return new_id(), (command.resource_type_name_vi or type_code).strip()

    def execute(self, command: CreateResourceCommand) -> CreateResourceResult:
        try:
            command.validate()
        except DomainError as exc:
            raise ResourceCreateError("metadata", "INVALID_METADATA", str(exc)) from None

        try:
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
        except UploadTooLargeError as exc:
            raise ResourceCreateError(
                "file_validation", "FILE_TOO_LARGE", str(exc), status_code=413
            ) from None
        except DomainError as exc:
            raise ResourceCreateError("file_validation", "INVALID_FILE", str(exc)) from None

        try:
            with self.uow() as repo:
                self._validate_metadata(repo, command)
        except DomainError as exc:
            raise ResourceCreateError("metadata", "INVALID_METADATA", str(exc)) from None
        except Exception:
            event(stage="ingest", action="RESOURCE_METADATA_LOOKUP_FAILED")
            raise ResourceCreateError(
                "persistence", "PERSISTENCE_ERROR", "Database unavailable", status_code=503
            ) from None

        try:
            stored = self.storage.save_stream(
                command.file_stream,
                max_size_bytes=validator.max_size_bytes,
                suffix=validator.extension,
                validate_path=validator.validate_path,
            )
        except InvalidFileContent as exc:
            raise ResourceCreateError("file_validation", "INVALID_FILE", str(exc)) from None
        except UploadTooLargeError as exc:
            raise ResourceCreateError(
                "file_validation", "FILE_TOO_LARGE", str(exc), status_code=413
            ) from None
        except DomainError as exc:
            raise ResourceCreateError("storage", "STORAGE_REJECTED", str(exc)) from None
        except OSError:
            event(stage="ingest", action="UPLOAD_STORAGE_FAILED")
            raise ResourceCreateError(
                "storage", "STORAGE_UNAVAILABLE", "Storage unavailable", status_code=503
            ) from None
        resource_id = new_id()
        version_id = new_id()
        stamp = now()
        try:
            with flow():
                with self.uow() as repo:
                    resource_type_id, resource_type_name = self._validate_metadata(repo, command)

                    repo.insert(
                        "master_learning_resources",
                        {
                            "id": resource_id,
                            "created_at": stamp,
                            "updated_at": stamp,
                            "provider_id": command.provider_id.strip(),
                            "provider_name": command.provider_name.strip(),
                            "provider_type": command.provider_type.strip(),
                            "provider_is_active": True,
                            "resource_type_id": resource_type_id,
                            "resource_type_code": command.resource_type_code.strip(),
                            "resource_type_name_vi": resource_type_name,
                            "resource_type_is_active": True,
                            "created_by": None,
                            "title": command.title.strip(),
                            "publication_status": command.publication_status,
                            "is_active": True,
                        },
                    )
                    repo.insert(
                        "resource_versions",
                        {
                            "id": version_id,
                            "created_at": stamp,
                            "updated_at": stamp,
                            "resource_id": resource_id,
                            "parent_version_id": None,
                            "version_number": 1,
                            "lifecycle_status": "INGESTED",
                            "storage_bucket": stored.bucket,
                            "storage_object_key": stored.object_key,
                            "source_hash": stored.source_hash,
                            "uploaded_by": None,
                        },
                    )
                    if command.subject_id:
                        repo.insert(
                            "resource_subjects",
                            {
                                "resource_id": resource_id,
                                "subject_id": command.subject_id,
                                "is_primary": True,
                            },
                        )
                    if command.grade_level_id:
                        repo.insert(
                            "resource_grade_levels",
                            {
                                "resource_id": resource_id,
                                "grade_level_id": command.grade_level_id,
                            },
                        )
        except DomainError as exc:
            # Content-addressed objects may already be shared by another
            # transaction. Leave a possible orphan for offline maintenance.
            event(stage="ingest", action="RESOURCE_CREATE_METADATA_FAILED", resource_id=resource_id)
            raise ResourceCreateError("metadata", "INVALID_METADATA", str(exc)) from None
        except Exception:
            event(
                stage="ingest", action="RESOURCE_CREATE_PERSISTENCE_FAILED", resource_id=resource_id
            )
            raise ResourceCreateError(
                "persistence",
                "PERSISTENCE_ERROR",
                "Database rejected the resource; verify metadata IDs and database migrations",
                status_code=503,
            ) from None

        event(
            resource_id=resource_id,
            resource_version_id=version_id,
            stage="ingest",
            action="RESOURCE_CREATED",
        )
        return CreateResourceResult(
            id=resource_id,
            title=command.title.strip(),
            resource_type_code=command.resource_type_code.strip(),
            media_category=validator.media_category,
            mime_type=command.declared_mime.lower().split(";", 1)[0].strip(),
            file_name=command.filename,
            file_size=command.file_size,
            source_hash=stored.source_hash,
            initial_version_id=version_id,
            version_number=1,
            lifecycle_status="INGESTED",
            created_at=stamp,
        )
