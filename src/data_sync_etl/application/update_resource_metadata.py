from data_sync_etl.domain.core import DomainError
from data_sync_etl.domain.resource_metadata import (
    ResourceMetadataOwnershipError,
    UpdateResourceMetadataCommand,
    assert_resource_metadata_editable,
)


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
    def __init__(self, uow, catalogs=None):
        self.uow, self.catalogs = uow, catalogs

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
                assert_resource_metadata_editable(resource)

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

                if self.catalogs is not None:
                    try:
                        if "provider_id" in values:
                            provider = self.catalogs.source_selection("providers", values["provider_id"])
                            if provider is not None:
                                if any(name in values and values[name] != provider[key] for name, key in (
                                        ("provider_name", "name"), ("provider_type", "provider_type"))):
                                    raise InvalidResourceMetadata("Provider metadata conflicts with SOURCE catalog")
                                values.update(provider_name=provider["name"], provider_type=provider["provider_type"])
                        if "resource_type_code" in values:
                            resource_type = self.catalogs.source_selection("types", values["resource_type_code"])
                            if resource_type is not None:
                                if "resource_type_name_vi" in values and values["resource_type_name_vi"] != resource_type["name"]:
                                    raise InvalidResourceMetadata("Resource type metadata conflicts with SOURCE catalog")
                                values.update(resource_type_name_vi=resource_type["name"], resource_type_id=resource_type["id"])
                    except DomainError as exc:
                        raise InvalidResourceMetadata(str(exc)) from None
                updated = repo.update_resource_metadata(command.resource_id, values)
                if "subject_id" in values:
                    repo.replace_subject_relation(command.resource_id, values["subject_id"])
                if "grade_level_id" in values:
                    repo.replace_grade_relation(command.resource_id, values["grade_level_id"])
                updated["uploaded_by"] = updated.get("created_by")
                updated["subject_id"] = repo.get_resource_subject_id(command.resource_id)
                updated["grade_level_id"] = repo.get_resource_grade_level_id(command.resource_id)
                return updated
        except (ResourceMetadataNotFound, InvalidResourceMetadata, ResourceMetadataOwnershipError):
            raise
        except Exception as exc:
            raise ResourceMetadataPersistenceError("Database rejected resource metadata") from exc
