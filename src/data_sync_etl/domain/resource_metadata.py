from dataclasses import dataclass, field

from data_sync_etl.domain.core import DomainError


class ResourceMetadataOwnershipError(DomainError):
    def __init__(self, source_origin):
        if source_origin == 'POSTGRESQL':
            self.code = 'RESOURCE_SOURCE_OWNED_READ_ONLY'
            message = 'Học liệu được quản lý từ SOURCE. Vui lòng sửa tại nguồn và đồng bộ lại.'
        else:
            self.code = 'RESOURCE_OWNERSHIP_UNVERIFIED'
            message = 'Chưa xác minh nguồn quản lý học liệu. Không thể cập nhật metadata tại Master.'
        super().__init__(message)


def assert_resource_metadata_editable(resource):
    if resource.get('source_origin') != 'LOCAL':
        raise ResourceMetadataOwnershipError(resource.get('source_origin'))


@dataclass(frozen=True)
class UpdateResourceMetadataCommand:
    resource_id: str
    supplied_fields: frozenset[str] = field(default_factory=frozenset)
    title: str | None = None
    resource_type_code: str | None = None
    resource_type_name_vi: str | None = None
    provider_id: str | None = None
    provider_name: str | None = None
    provider_type: str | None = None
    subject_id: str | None = None
    grade_level_id: str | None = None
    publication_status: str | None = None
    uploaded_by: str | None = None

    LIMITS = {
        "title": 500,
        "resource_type_code": 50,
        "resource_type_name_vi": 150,
        "provider_id": 30,
        "provider_name": 255,
        "provider_type": 30,
        "subject_id": 30,
        "grade_level_id": 30,
        "uploaded_by": 30,
    }
    REQUIRED_IF_SENT = {
        "title", "resource_type_code", "resource_type_name_vi",
        "provider_id", "provider_name", "provider_type",
    }

    def validate(self) -> None:
        if not self.resource_id or not self.resource_id.strip():
            raise DomainError("resource_id is required")
        if not self.supplied_fields:
            raise DomainError("At least one metadata field is required")
        unknown = self.supplied_fields - set(self.LIMITS) - {"publication_status"}
        if unknown:
            raise DomainError("Unsupported metadata field")

        for field_name in self.supplied_fields:
            value = getattr(self, field_name)
            if value is None:
                if field_name in self.REQUIRED_IF_SENT:
                    raise DomainError(f"{field_name} cannot be null")
                continue
            if not isinstance(value, str):
                raise DomainError(f"{field_name} must be a string")
            value = value.strip()
            if field_name in self.REQUIRED_IF_SENT and not value:
                raise DomainError(f"{field_name} cannot be blank")
            if field_name in self.LIMITS and len(value) > self.LIMITS[field_name]:
                raise DomainError(f"{field_name} exceeds maximum length")

        if "publication_status" in self.supplied_fields and self.publication_status not in {
            "DRAFT", "PUBLISHED", "UNPUBLISHED"
        }:
            raise DomainError("Invalid publication_status")
