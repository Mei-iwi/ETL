from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.resource_metadata import UpdateResourceMetadataCommand


class ResourceMetadataNotFound(DomainError):
    pass


class InvalidResourceMetadata(DomainError):
    pass


class ResourceMetadataPersistenceError(DomainError):
    pass


# Backwards-compatible names for existing unit tests and callers.
InvalidMetadata = InvalidResourceMetadata
ResourceNotFound = ResourceMetadataNotFound


class UpdateResourceMetadataUseCase:
    def __init__(self, uow):
        self.uow = uow

    def execute(self, command: UpdateResourceMetadataCommand) -> dict:
        try:
            command.validate()
        except DomainError as exc:
            raise InvalidResourceMetadata(str(exc)) from None

        values = {}
        for field_name in command.supplied_fields:
            value = getattr(command, field_name)
            values[field_name] = value.strip() if isinstance(value, str) else value

        try:
            with self.uow() as repo:
                resource = repo.get_resource_for_update(command.resource_id)
                if resource is None:
                    raise ResourceMetadataNotFound("Resource not found")

                subject_id = values.get(
                    "subject_id", repo.get_resource_subject_id(command.resource_id)
                )
                grade_level_id = values.get(
                    "grade_level_id", repo.get_resource_grade_level_id(command.resource_id)
                )

                subject = None
                if subject_id is not None:
                    subject = repo.get("subjects", subject_id)
                    if not subject or not subject["is_active"] or subject["deleted_at"] is not None:
                        raise InvalidResourceMetadata("Invalid subject_id")

                if grade_level_id is not None:
                    grade = repo.get("master_grade_levels", grade_level_id)
                    if not grade or not grade["grade_is_active"] or grade["grade_deleted_at"] is not None:
                        raise InvalidResourceMetadata("Invalid grade_level_id")
                    if subject is not None and subject.get("grade_id") not in {None, grade_level_id}:
                        raise InvalidResourceMetadata("Subject does not belong to grade_level_id")

                updated = repo.update_resource_metadata(command.resource_id, values)
                if "subject_id" in values:
                    repo.replace_subject_relation(command.resource_id, values["subject_id"])
                if "grade_level_id" in values:
                    repo.replace_grade_relation(command.resource_id, values["grade_level_id"])
                updated["uploaded_by"] = updated.get("created_by")
                updated["subject_id"] = repo.get_resource_subject_id(command.resource_id)
                updated["grade_level_id"] = repo.get_resource_grade_level_id(command.resource_id)
                return updated
        except (ResourceMetadataNotFound, InvalidResourceMetadata):
            raise
        except Exception as exc:
            raise ResourceMetadataPersistenceError("Database rejected resource metadata") from exc
