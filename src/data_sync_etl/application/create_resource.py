from data_sync_etl.domain.core import DomainError, new_id, now
from data_sync_etl.domain.format_validation import FormatValidatorRegistry
from data_sync_etl.domain.resource import CreateResourceCommand, CreateResourceResult
from data_sync_etl.logging import event, flow
from data_sync_etl.ports.processing import StoragePort
from data_sync_etl.ports.repositories import UnitOfWork


class CreateResourceUseCase:
    def __init__(self, uow: UnitOfWork, storage: StoragePort, format_registry: FormatValidatorRegistry):
        self.uow = uow
        self.storage = storage
        self.format_registry = format_registry

    def execute(self, command: CreateResourceCommand) -> CreateResourceResult:
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

        stored = self.storage.save_stream(
            command.file_stream,
            max_size_bytes=validator.max_size_bytes,
            suffix=validator.extension,
        )
        resource_id = new_id()
        version_id = new_id()
        stamp = now()
        try:
            with flow():
                with self.uow() as repo:
                    if command.subject_id:
                        subject = repo.get("subjects", command.subject_id)
                        if not subject or not subject["is_active"] or subject["deleted_at"] is not None:
                            raise DomainError("Subject not found or inactive")
                    if command.grade_level_id:
                        grade = repo.get("master_grade_levels", command.grade_level_id)
                        if (
                            not grade
                            or not grade["grade_is_active"]
                            or grade["grade_deleted_at"] is not None
                        ):
                            raise DomainError("Grade level not found or inactive")

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
                            "resource_type_id": new_id(),
                            "resource_type_code": command.resource_type_code.strip(),
                            "resource_type_name_vi": (
                                command.resource_type_name_vi or command.resource_type_code
                            ).strip(),
                            "resource_type_is_active": True,
                            "created_by": command.uploaded_by,
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
                            "uploaded_by": command.uploaded_by,
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
        except DomainError:
            if stored.created:
                try:
                    self.storage.delete(stored.bucket, stored.object_key)
                except Exception:
                    pass
            raise
        except Exception:
            if stored.created:
                try:
                    self.storage.delete(stored.bucket, stored.object_key)
                except Exception:
                    pass
            raise DomainError("Unable to create resource") from None

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
